"""
dbv2 — 基于 pymsg 协议（MsgServer）的数据库服务。

替代 Flask HTTP 接口，通过 TCP 长度前缀帧收发 JSON。
协议：每条消息为完整 JSON，新增 ``_route`` 字段对应原 HTTP 路由路径。

请求示例::

    {
        "_route": "execute",
        "action": "select",
        "table": "Users",
        "where": {"age": {"gt": 20}},
        "order_by": [{"column": "id", "desc": true}],
        "limit": 10
    }

    {
        "_route": "log/ingest",
        "logs": [{"service": "waf", "level": "INFO", "message": "..."}]
    }

支持的路由::

    execute      → 原 /execute
    log/ingest   → 原 /log/ingest
    log/query    → 原 /log/query
    log/stats    → 原 /log/stats
    log/purge    → 原 /log/purge
    log/health   → 原 /log/health
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    create_engine, MetaData, Table,
    select, insert, update, delete, func, and_,
)
from sqlalchemy.exc import SQLAlchemyError

# pymsg 导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import MsgServer  # noqa: E402

# ── 数据库配置 ──────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")

# ── 日志模块常量 ────────────────────────────────────────────────────────

VALID_SERVICES = {
    "waf", "gateway", "static", "dnfs", "db",
    "ai_review", "captcha", "core",
}
VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

MAX_BATCH = 100
MAX_MESSAGE_LEN = 8192
MAX_EXTRA_LEN = 4096

DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_RECORDS = 1_000_000
DEFAULT_CLEANUP_INTERVAL = 3600  # 秒


# ═══════════════════════════════════════════════════════════════════════
# DBServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class DBServer:
    """基于 pymsg 的数据库服务，通过 TCP 长连接收发 JSON。"""

    def __init__(self, addr="127.0.0.1", port=8005):
        self.addr = addr
        self.port = port
        self.engine = create_engine(DATABASE_URL)
        self.metadata = MetaData()
        self._server = MsgServer(addr, port)

        # 日志模块内部状态
        self._logs_table_cache = {}

        # 路由表
        self._routes = {
            "execute":    self._handle_execute,
            "log/ingest": self._handle_log_ingest,
            "log/query":  self._handle_log_query,
            "log/stats":  self._handle_log_stats,
            "log/purge":  self._handle_log_purge,
            "log/health": self._handle_log_health,
        }

    # ── 启动 / 停止 ──────────────────────────────────────────────────
    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        print(f"[dbv2] 监听 tcp://{self.addr}:{self.port}")
        self._start_cleanup_thread()
        self._server.start(self._dispatch)

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ── 消息调度 -------------------------------------------------------
    def _dispatch(self, sock, addr, text):
        """解析 JSON 中的 _route，分发给对应的 handler，并回复结果。"""
        try:
            req = json.loads(text)
        except json.JSONDecodeError as e:
            self._reply(sock, False, error=f"JSON 解析失败: {e}")
            return

        route = req.pop("_route", "")
        handler = self._routes.get(route)
        if handler is None:
            self._reply(sock, False, error=f"未知路由: {route}")
            return

        try:
            result = handler(req, sock=sock)
            self._reply(sock, True, data=result)
        except Exception as e:
            traceback.print_exc()
            self._reply(sock, False, error=str(e))

    def _reply(self, sock, success, data=None, error=None):
        resp = {"success": success}
        if data is not None:
            resp["data"] = data
        if error is not None:
            resp["error"] = error
        self._server.reply(sock, json.dumps(resp, ensure_ascii=False, default=str))

    # ── 表反射 ---------------------------------------------------------
    def _get_table(self, table_name):
        if table_name not in self.metadata.tables:
            Table(table_name, self.metadata, autoload_with=self.engine)
        return self.metadata.tables[table_name]

    def _build_where(self, table, conditions):
        if not conditions:
            return None
        clauses = []
        for col, val in conditions.items():
            column = table.columns[col]
            if isinstance(val, dict):
                if "eq" in val:
                    clauses.append(column == val["eq"])
                if "ne" in val:
                    clauses.append(column != val["ne"])
                if "gt" in val:
                    clauses.append(column > val["gt"])
                if "lt" in val:
                    clauses.append(column < val["lt"])
                if "ge" in val:
                    clauses.append(column >= val["ge"])
                if "le" in val:
                    clauses.append(column <= val["le"])
                if "like" in val:
                    clauses.append(column.like(val["like"]))
                if "in" in val:
                    clauses.append(column.in_(val["in"]))
            else:
                clauses.append(column == val)
        return and_(*clauses)

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理 — execute
    # ═══════════════════════════════════════════════════════════════════

    def _handle_execute(self, data, **kw):
        action = data.get("action", "select")
        table_name = data.get("table")
        if not table_name:
            raise ValueError("缺少 table 字段")

        try:
            table = self._get_table(table_name)
        except Exception as e:
            raise ValueError(f"表加载失败: {e}")

        with self.engine.connect() as conn:
            if action == "select":
                return self._do_select(conn, table, data)
            elif action == "insert":
                return self._do_insert(conn, table, data)
            elif action == "update":
                return self._do_update(conn, table, data)
            elif action == "delete":
                return self._do_delete(conn, table, data)
            else:
                raise ValueError(f"不支持的操作: {action}")

    def _do_select(self, conn, table, data):
        columns = data.get("columns")
        if columns:
            cols = [table.columns[c] for c in columns]
        else:
            cols = [table]

        stmt = select(*cols)
        where_cond = self._build_where(table, data.get("where"))
        if where_cond is not None:
            stmt = stmt.where(where_cond)

        order_by = data.get("order_by")
        if order_by:
            for item in order_by:
                col = table.columns[item["column"]]
                stmt = stmt.order_by(col.desc() if item.get("desc") else col.asc())

        limit = data.get("limit")
        if limit is not None:
            stmt = stmt.limit(limit)
        offset = data.get("offset")
        if offset is not None:
            stmt = stmt.offset(offset)

        res = conn.execute(stmt)
        return [dict(row._mapping) for row in res.fetchall()]

    def _do_insert(self, conn, table, data):
        values = data.get("values", {})
        stmt = insert(table).values(**values)
        res = conn.execute(stmt)
        conn.commit()
        return [{"lastrowid": res.lastrowid}] if hasattr(res, "lastrowid") else []

    def _do_update(self, conn, table, data):
        values = data.get("values", {})
        where_cond = self._build_where(table, data.get("where"))
        stmt = update(table).values(**values)
        if where_cond is not None:
            stmt = stmt.where(where_cond)
        res = conn.execute(stmt)
        conn.commit()
        return [{"rowcount": res.rowcount}]

    def _do_delete(self, conn, table, data):
        where_cond = self._build_where(table, data.get("where"))
        stmt = delete(table)
        if where_cond is not None:
            stmt = stmt.where(where_cond)
        res = conn.execute(stmt)
        conn.commit()
        return [{"rowcount": res.rowcount}]

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理 — log/*
    # ═══════════════════════════════════════════════════════════════════

    # ── 日志表管理 ────────────────────────────────────────────────────
    def _ensure_logs_table(self):
        if "Logs" in self._logs_table_cache:
            return self._logs_table_cache["Logs"]

        with self.engine.connect() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS Logs (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT    NOT NULL,
                    service   TEXT    NOT NULL,
                    level     TEXT    NOT NULL,
                    message   TEXT    NOT NULL,
                    trace_id  TEXT,
                    extra     TEXT
                )
            """)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_logs_service_ts ON Logs(service, ts)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_logs_level_ts  ON Logs(level, ts)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_logs_ts         ON Logs(ts)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_logs_trace      ON Logs(trace_id)"
            )
            conn.commit()

        table = Table("Logs", self.metadata, autoload_with=self.engine)
        self._logs_table_cache["Logs"] = table
        return table

    # ── 内部日志（直接写库，无 HTTP）──────────────────────────────────
    def _log_internal(self, service, level, message,
                      trace_id=None, extra=None):
        try:
            table = self._ensure_logs_table()
            extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
            if len(message) > MAX_MESSAGE_LEN:
                message = message[:MAX_MESSAGE_LEN]
            if extra_json and len(extra_json) > MAX_EXTRA_LEN:
                extra_json = extra_json[:MAX_EXTRA_LEN]

            with self.engine.connect() as conn:
                conn.execute(insert(table).values(
                    ts=datetime.now(timezone.utc).isoformat(),
                    service=service,
                    level=level,
                    message=message,
                    trace_id=trace_id,
                    extra=extra_json,
                ))
                conn.commit()
        except Exception:
            pass

    # ── log/ingest ────────────────────────────────────────────────────
    def _handle_log_ingest(self, data, **kw):
        logs = data.get("logs")
        if not isinstance(logs, list) or not logs:
            raise ValueError("logs 必须是非空数组")
        if len(logs) > MAX_BATCH:
            raise ValueError(f"单次最多 {MAX_BATCH} 条，当前 {len(logs)} 条")

        table = self._ensure_logs_table()
        now = datetime.now(timezone.utc).isoformat()
        rows = []

        for i, log in enumerate(logs):
            if not isinstance(log, dict):
                raise ValueError(f"第 {i} 条日志不是有效对象")

            service = log.get("service")
            level = log.get("level")
            message = log.get("message")

            if not service or service not in VALID_SERVICES:
                raise ValueError(f"第 {i} 条: service 无效或缺失")
            if not level or level not in VALID_LEVELS:
                raise ValueError(f"第 {i} 条: level 无效或缺失")
            if not message or not isinstance(message, str):
                raise ValueError(f"第 {i} 条: message 缺失或非字符串")

            if len(message) > MAX_MESSAGE_LEN:
                message = message[:MAX_MESSAGE_LEN]

            trace_id = log.get("trace_id")
            extra = log.get("extra")
            extra_json = None
            if extra is not None:
                extra_json = json.dumps(extra, ensure_ascii=False)
                if len(extra_json) > MAX_EXTRA_LEN:
                    extra_json = extra_json[:MAX_EXTRA_LEN]

            rows.append({
                "ts": log.get("ts") or now,
                "service": service,
                "level": level,
                "message": message,
                "trace_id": trace_id,
                "extra": extra_json,
            })

        with self.engine.connect() as conn:
            conn.execute(insert(table), rows)
            conn.commit()

        return {"inserted": len(rows)}

    # ── log/query ─────────────────────────────────────────────────────
    def _handle_log_query(self, data, **kw):
        table = self._ensure_logs_table()
        clauses = []

        svc = data.get("service")
        if svc:
            col = table.columns["service"]
            clauses.append(col.in_(svc) if isinstance(svc, list) else col == svc)

        lvl = data.get("level")
        if lvl:
            col = table.columns["level"]
            clauses.append(col.in_(lvl) if isinstance(lvl, list) else col == lvl)

        tid = data.get("trace_id")
        if tid:
            clauses.append(table.columns["trace_id"] == tid)

        since = data.get("since")
        until = data.get("until")
        ts_col = table.columns["ts"]
        if since:
            clauses.append(ts_col >= since)
        if until:
            clauses.append(ts_col <= until)

        keyword = data.get("keyword")
        if keyword:
            clauses.append(table.columns["message"].like(f"%{keyword}%"))

        where_cond = and_(*clauses) if clauses else None

        order_col_name = data.get("order_by", "ts")
        if order_col_name not in ("id", "ts", "service", "level"):
            order_col_name = "ts"
        order_col = table.columns[order_col_name]
        order_expr = order_col.desc() if data.get("desc", True) else order_col.asc()

        limit = min(data.get("limit", 100), 1000)
        offset = data.get("offset", 0)

        with self.engine.connect() as conn:
            count_stmt = select(func.count()).select_from(table)
            if where_cond is not None:
                count_stmt = count_stmt.where(where_cond)
            total = conn.execute(count_stmt).scalar() or 0

            stmt = select(table)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            stmt = stmt.order_by(order_expr).limit(limit).offset(offset)

            rows = conn.execute(stmt).fetchall()
            logs = []
            for row in rows:
                d = dict(row._mapping)
                if d.get("extra"):
                    try:
                        d["extra"] = json.loads(d["extra"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                logs.append(d)

        return {"total": total, "logs": logs}

    # ── log/stats ─────────────────────────────────────────────────────
    def _handle_log_stats(self, data, **kw):
        table = self._ensure_logs_table()
        group_by = data.get("group_by", "service")
        group_fields = [g.strip() for g in group_by.split(",") if g.strip()]
        for g in group_fields:
            if g not in ("service", "level"):
                raise ValueError(f"group_by 仅支持 service / level，收到: {g}")

        clauses = []
        since = data.get("since")
        until = data.get("until")
        ts_col = table.columns["ts"]
        if since:
            clauses.append(ts_col >= since)
        if until:
            clauses.append(ts_col <= until)
        where_cond = and_(*clauses) if clauses else None

        with self.engine.connect() as conn:
            cols = [table.columns[g] for g in group_fields] + [func.count().label("count")]
            stmt = select(*cols)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            stmt = stmt.group_by(*[table.columns[g] for g in group_fields])

            rows = conn.execute(stmt).fetchall()
            groups = [dict(row._mapping) for row in rows]

            count_stmt = select(func.count()).select_from(table)
            if where_cond is not None:
                count_stmt = count_stmt.where(where_cond)
            total = conn.execute(count_stmt).scalar() or 0

        return {"groups": groups, "total": total}

    # ── log/purge ─────────────────────────────────────────────────────
    def _handle_log_purge(self, data, **kw):
        before = data.get("before")
        if not before:
            raise ValueError("缺少 before 字段")

        table = self._ensure_logs_table()
        with self.engine.connect() as conn:
            stmt = delete(table).where(table.columns["ts"] < before)
            result = conn.execute(stmt)
            conn.commit()
        deleted = result.rowcount
        self._log_internal("db", "INFO",
                           f"purge 清理 {deleted} 条 (before={before})")
        return {"deleted": deleted}

    # ── log/health ────────────────────────────────────────────────────
    def _handle_log_health(self, data, **kw):
        table = self._ensure_logs_table()
        with self.engine.connect() as conn:
            count = conn.execute(
                select(func.count()).select_from(table)
            ).scalar() or 0
        return {"status": "ok", "count": count}

    # ── 后台自动清理线程 ──────────────────────────────────────────────
    def _start_cleanup_thread(self):
        def _loop():
            while True:
                time.sleep(DEFAULT_CLEANUP_INTERVAL)
                try:
                    self._do_cleanup()
                except Exception:
                    pass

        import threading
        t = threading.Thread(target=_loop, daemon=True, name="dbv2-cleanup")
        t.start()

    def _do_cleanup(self):
        table = self._ensure_logs_table()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=DEFAULT_RETENTION_DAYS)
        ).isoformat()

        with self.engine.connect() as conn:
            result = conn.execute(
                delete(table).where(table.columns["ts"] < cutoff)
            )
            conn.commit()
            expired_deleted = result.rowcount

            count = conn.execute(
                select(func.count()).select_from(table)
            ).scalar() or 0
            trimmed = 0
            if count > DEFAULT_MAX_RECORDS:
                excess = count - DEFAULT_MAX_RECORDS
                cutoff_row = conn.execute(
                    select(table.columns["ts"])
                    .order_by(table.columns["ts"].asc())
                    .limit(1).offset(DEFAULT_MAX_RECORDS)
                ).fetchone()
                if cutoff_row:
                    trim_result = conn.execute(
                        delete(table).where(table.columns["ts"] < cutoff_row[0])
                    )
                    conn.commit()
                    trimmed = trim_result.rowcount

        if expired_deleted or trimmed:
            self._log_internal(
                "db", "INFO",
                f"自动清理: 过期删除 {expired_deleted} 条, 超量裁剪 {trimmed} 条"
            )


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="dbv2 — pymsg 数据库服务")
    parser.add_argument("--addr", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8005, help="监听端口")
    args = parser.parse_args()

    server = DBServer(args.addr, args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
