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

支持的路由::

    execute  → 通用 CRUD（select / insert / update / delete）

日期/时间列::

    values 与 where 中的 DATETIME / DATE / TIME 列接受 ISO 8601 字符串
    （如 "2026-07-28T22:30:00"，可带时区偏移，统一转 UTC 存储），
    或哨兵值 "$now"（服务端当前 UTC 时间）。
    响应中的日期时间一律序列化为 ISO 8601（T 分隔）。
"""

import configparser
import json
import os
import threading
import time
import traceback
from datetime import datetime, date, time as dt_time, timezone, timedelta

from sqlalchemy import (
    create_engine, MetaData, Table,
    select, insert, update, delete, func, and_,
    event,
)
from sqlalchemy import types as sa_types
from sqlalchemy.exc import SQLAlchemyError

# pymsg 导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcServer  # noqa: E402

# ── 配置加载（三层 fail-safe：内置默认值 → confs → 环境变量覆盖）──────
# 加载顺序：内置默认值 → confs 统一配置服务（db.ini，优先）；
# confs 不可用时 fail-safe 使用默认值；环境变量优先级最高（Docker 兼容）。

_db_config = {
    'server': {'host': '0.0.0.0', 'port': '8005', 'workers': '4'},
    'database': {'url': 'sqlite:///database.db'},
    'logs': {
        'retention_days': '30',
        'max_records': '1000000',
        'cleanup_interval': '3600',
    },
    'confs': {'host': '127.0.0.1', 'port': '8010'},
}


def _merge_ini(text):
    """把 ini 文本合并进 _db_config（仅覆盖已知的 section/key）。"""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read_string(text)
    for section in _db_config:
        if ini.has_section(section):
            for key, value in ini[section].items():
                if key in _db_config[section]:
                    _db_config[section][key] = value


def _fetch_from_confs():
    """从 confs 统一配置服务拉取 db.ini 全文，失败返回 None（fail-safe）。"""
    try:
        from pymsg import RpcClient
        host = os.environ.get('CONFS_HOST', _db_config['confs']['host'])
        port = int(os.environ.get('CONFS_PORT', _db_config['confs']['port']))
        data = RpcClient(host, port, 'db', timeout=3).call(
            'get', {'name': 'db.ini'})
        return data.get('content')
    except Exception:
        return None


_confs_text = _fetch_from_confs()
_config_source = 'built-in defaults'
if _confs_text is not None:
    _merge_ini(_confs_text)
    _config_source = 'confs (db.ini)'
    print(f'[dbv2] 配置来源: confs 统一配置服务 (db.ini)')
else:
    print(f'[dbv2] 警告: confs 不可用，使用内置默认值')

# 环境变量覆盖（最高优先级，保持 Docker 兼容）
if os.environ.get('LISTEN_ADDR'):
    _db_config['server']['host'] = os.environ['LISTEN_ADDR']
if os.environ.get('LISTEN_PORT'):
    _db_config['server']['port'] = os.environ['LISTEN_PORT']
if os.environ.get('WORKERS'):
    _db_config['server']['workers'] = os.environ['WORKERS']
if os.environ.get('DATABASE_URL'):
    _db_config['database']['url'] = os.environ['DATABASE_URL']

# 解析为最终常量
DATABASE_URL = _db_config['database']['url']
DEFAULT_RETENTION_DAYS = int(_db_config['logs']['retention_days'])
DEFAULT_MAX_RECORDS = int(_db_config['logs']['max_records'])
DEFAULT_CLEANUP_INTERVAL = int(_db_config['logs']['cleanup_interval'])

# ── SQLite 连接配置（针对 Docker/多线程环境优化）──────────────────────

def _configure_sqlite(dbapi_connection, connection_record):
    """为每个 SQLite 连接设置 PRAGMA，优化并发与持久化性能。

    - WAL 模式：读写不互斥，并发性能大幅提升，Docker 卷挂载下更稳定
    - NORMAL 同步级别：在断电时可能丢失最近提交的事务（但不会损坏数据库）
      配合 WAL checkpoint 足够安全，性能显著提升
    - foreign_keys=ON：启用外键约束
    - busy_timeout：锁等待超时 5 秒，避免 SQLITE_BUSY 错误
    - mmap_size：使用内存映射 I/O 提升读取性能
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA mmap_size=268435456")  # 256MB
    cursor.close()

# ── 日志模块常量 ────────────────────────────────────────────────────────

MAX_MESSAGE_LEN = 8192   # message 上限 8KB
MAX_EXTRA_LEN = 4096     # extra 上限 4KB

#: where/values 中的哨兵值：服务端当前 UTC 时间
NOW_SENTINEL = "$now"


def _json_default(o):
    """json.dumps 兜底序列化：日期时间一律 ISO 8601（T 分隔），其余 str()。"""
    if isinstance(o, (datetime, date, dt_time)):
        return o.isoformat()
    return str(o)


# ═══════════════════════════════════════════════════════════════════════
# DBServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class DBServer:
    """基于 pymsg 的数据库服务，通过 TCP 长连接收发 JSON。"""

    def __init__(self, addr="127.0.0.1", port=8005, workers=4):
        self.addr = addr
        self.port = port
        self.engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
        )
        # 为 SQLite 连接应用 PRAGMA 优化
        if DATABASE_URL.startswith("sqlite"):
            event.listen(self.engine, "connect", _configure_sqlite)
        self.metadata = MetaData()
        self._server = RpcServer(
            addr, port, workers=workers,
            json_default=_json_default,
            on_error=lambda route, e: traceback.print_exc(),
        )
        self._server.add_route("execute", self._handle_execute)

        # 日志表缓存（避免重复反射）
        self._logs_table_cache = {}

        # 业务表缓存：仅缓存"已完整反射"的表。
        # 注意不能用 metadata.tables 作完成标记 —— Table(autoload_with=...)
        # 会先注册进 metadata.tables（此时无列）再填充列，锁外快路径可能
        # 读到反射半途的空列表。
        self._tables_cache = {}

        # 表反射锁：workers>0 时多个 worker 线程可能对同一新表并发做
        # check-then-act 反射（Table(..., autoload_with=...) 对已定义表名会
        # 抛 InvalidRequestError），反射操作需串行化
        self._reflect_lock = threading.Lock()

    # ── 启动 / 停止 ──────────────────────────────────────────────────
    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        print(f"[dbv2] 监听 tcp://{self.addr}:{self.port}")
        self._ensure_logs_table()   # 确保 Logs 表已建好（外部写日志前）
        self._start_cleanup_thread()
        self._server.start()

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ── 表反射 ---------------------------------------------------------
    def _get_table(self, table_name):
        # double-check：_tables_cache 只在反射完整完成后写入（dict 读写 GIL
        # 下安全），锁外快路径读到的要么是完整表要么是 None
        table = self._tables_cache.get(table_name)
        if table is None:
            with self._reflect_lock:
                table = self._tables_cache.get(table_name)
                if table is None:
                    # 持锁期间无其他反射在进行，metadata 中的表必然完整
                    table = self.metadata.tables.get(table_name)
                    if table is None:
                        table = Table(table_name, self.metadata,
                                      autoload_with=self.engine)
                    self._tables_cache[table_name] = table
        return table

    # ── 日期/时间类型适配 ------------------------------------------------
    def _coerce_value(self, column, value):
        """按反射列类型把 JSON 值适配为 SQLAlchemy 需要的 Python 对象。

        JSON 没有日期类型，客户端只能传字符串；而 SQLite 的 DATETIME/DATE
        类型只接受 Python datetime/date 对象，字符串会被直接拒绝
        （TypeError: SQLite DateTime type only accepts Python datetime ...）。
        这里统一把 ISO 8601 字符串转为对应对象；带时区偏移的一律转 UTC
        并去掉 tzinfo，与 SQLite 朴素存储保持一致。
        """
        if not isinstance(value, str):
            return value

        col_type = column.type
        if isinstance(col_type, sa_types.DateTime):
            if value == NOW_SENTINEL:
                return datetime.now(timezone.utc).replace(tzinfo=None)
            text = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                raise ValueError(
                    f"列 {column.name} 的日期时间值无法解析"
                    f"（需 ISO 8601 或 \"{NOW_SENTINEL}\"）: {value!r}"
                )
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        if isinstance(col_type, sa_types.Date):
            if value == NOW_SENTINEL:
                return datetime.now(timezone.utc).date()
            try:
                return date.fromisoformat(value)
            except ValueError:
                raise ValueError(
                    f"列 {column.name} 的日期值无法解析"
                    f"（需 ISO 8601 或 \"{NOW_SENTINEL}\"）: {value!r}"
                )
        if isinstance(col_type, sa_types.Time):
            try:
                return dt_time.fromisoformat(value)
            except ValueError:
                raise ValueError(
                    f"列 {column.name} 的时间值无法解析"
                    f"（需 ISO 8601）: {value!r}"
                )
        return value

    def _build_where(self, table, conditions):
        if not conditions:
            return None
        clauses = []
        for col, val in conditions.items():
            column = table.columns[col]
            if isinstance(val, dict):
                if "eq" in val:
                    clauses.append(column == self._coerce_value(column, val["eq"]))
                if "ne" in val:
                    clauses.append(column != self._coerce_value(column, val["ne"]))
                if "gt" in val:
                    clauses.append(column > self._coerce_value(column, val["gt"]))
                if "lt" in val:
                    clauses.append(column < self._coerce_value(column, val["lt"]))
                if "ge" in val:
                    clauses.append(column >= self._coerce_value(column, val["ge"]))
                if "le" in val:
                    clauses.append(column <= self._coerce_value(column, val["le"]))
                if "like" in val:
                    # like 是字符串模式匹配，不做日期转换
                    clauses.append(column.like(val["like"]))
                if "in" in val:
                    clauses.append(column.in_(
                        [self._coerce_value(column, v) for v in val["in"]]
                    ))
            else:
                clauses.append(column == self._coerce_value(column, val))
        return and_(*clauses)

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理 — execute
    # ═══════════════════════════════════════════════════════════════════

    def _handle_execute(self, data):
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
        values = {
            k: self._coerce_value(table.columns[k], v)
            for k, v in values.items()
        }
        stmt = insert(table).values(**values)
        res = conn.execute(stmt)
        conn.commit()
        return [{"lastrowid": res.lastrowid}] if hasattr(res, "lastrowid") else []

    def _do_update(self, conn, table, data):
        values = data.get("values", {})
        values = {
            k: self._coerce_value(table.columns[k], v)
            for k, v in values.items()
        }
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
    # 日志表管理 + 后台清理
    # ═══════════════════════════════════════════════════════════════════

    # ── 日志表管理 ────────────────────────────────────────────────────
    def _ensure_logs_table(self):
        if "Logs" in self._logs_table_cache:
            return self._logs_table_cache["Logs"]

        # 与 _get_table 共用反射锁，double-check 保证并发下只建/反射一次；
        # Logs 可能已被 _get_table("Logs") 反射进 metadata，需幂等处理
        with self._reflect_lock:
            if "Logs" in self._logs_table_cache:
                return self._logs_table_cache["Logs"]

            table = self.metadata.tables.get("Logs")
            if table is None:
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

    default_addr = _db_config['server']['host']
    default_port = int(_db_config['server']['port'])
    default_workers = int(_db_config['server']['workers'])

    parser = argparse.ArgumentParser(description="dbv2 — pymsg 数据库服务")
    parser.add_argument("--addr", default=default_addr, help="监听地址")
    parser.add_argument("--port", type=int, default=default_port, help="监听端口")
    parser.add_argument("--workers", type=int, default=default_workers,
                        help="回调并发线程数（0=同步串行模式）")
    args = parser.parse_args()

    server = DBServer(args.addr, args.port, workers=args.workers)
    print(f'[dbv2] 配置来源: {_config_source}')
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
