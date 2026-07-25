"""
db 服务的内置日志子模块。

提供两部分能力：
  1. log_internal()      — db 进程内部直接写 Logs 表（无 HTTP 往返，无循环依赖）
  2. /log/* HTTP 路由     — 供外部服务推送 / 查询 / 统计 / 清理日志

设计要点（防递归）：
  - log_internal() 直接操作数据库，不经过任何 HTTP 路由
  - /log/ingest 成功后不再调 log_internal
  - /log/ingest 失败时才调 log_internal，而 log_internal 不会回调 /log/ingest
"""

import json
import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify
from sqlalchemy import (
    Table, insert, select, delete, func, and_, or_, MetaData
)

bp = Blueprint('log', __name__, url_prefix='/log')

# ── 常量 ──────────────────────────────────────────────────────────────

VALID_SERVICES = {
    'waf', 'gateway', 'static', 'dnfs', 'db',
    'ai_review', 'captcha', 'core',
}
VALID_LEVELS = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}

MAX_BATCH = 100          # 单次 /log/ingest 最多 100 条
MAX_MESSAGE_LEN = 8192   # message 上限 8KB
MAX_EXTRA_LEN = 4096     # extra 上限 4KB

DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_RECORDS = 1_000_000
DEFAULT_CLEANUP_INTERVAL = 3600  # 秒

# ── 表管理 ─────────────────────────────────────────────────────────────

_LOGS_TABLE_CACHE = {}

def _ensure_logs_table(metadata: MetaData, engine):
    """确保 Logs 表已建好并已反射到 metadata（首次调用时建表 + 反射）。"""
    if 'Logs' in _LOGS_TABLE_CACHE:
        return _LOGS_TABLE_CACHE['Logs']

    # 先用原生 SQL 建表（SQLAlchemy 反射要求表已存在）
    with engine.connect() as conn:
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
            "CREATE INDEX IF NOT EXISTS idx_logs_level_ts ON Logs(level, ts)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_logs_ts ON Logs(ts)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_logs_trace ON Logs(trace_id)"
        )
        conn.commit()

    # 反射到 metadata
    table = Table('Logs', metadata, autoload_with=engine)
    _LOGS_TABLE_CACHE['Logs'] = table
    return table


# ── 内部日志记录（db 自己用，无 HTTP）─────────────────────────────────

def log_internal(engine, metadata, service, level, message,
                 trace_id=None, extra=None):
    """
    db 进程内部记录日志 — 直接操作数据库，不走 HTTP，无循环依赖。

    供 db 的 /execute 路由内部调用。失败时静默吞掉，不影响主业务。
    """
    try:
        table = _ensure_logs_table(metadata, engine)
        extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
        if len(message) > MAX_MESSAGE_LEN:
            message = message[:MAX_MESSAGE_LEN]
        if extra_json and len(extra_json) > MAX_EXTRA_LEN:
            extra_json = extra_json[:MAX_EXTRA_LEN]

        with engine.connect() as conn:
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
        # 日志记录失败不应影响主业务
        pass


# ── HTTP 路由 ──────────────────────────────────────────────────────────

def init_log_routes(app, engine, metadata,
                    retention_days=DEFAULT_RETENTION_DAYS,
                    max_records=DEFAULT_MAX_RECORDS,
                    cleanup_interval=DEFAULT_CLEANUP_INTERVAL):
    """把日志蓝图注册到 Flask app，并注入 engine/metadata。"""
    print('[logs] 正在初始化日志模块 ...')

    app.config['LOG_ENGINE'] = engine
    app.config['LOG_METADATA'] = metadata
    app.config['LOG_RETENTION_DAYS'] = retention_days
    app.config['LOG_MAX_RECORDS'] = max_records
    app.config['LOG_CLEANUP_INTERVAL'] = cleanup_interval

    # 建表 + 反射
    try:
        table = _ensure_logs_table(metadata, engine)
        with engine.connect() as conn:
            count = conn.execute(
                select(func.count()).select_from(table)
            ).scalar() or 0
        print(f'[logs] Logs 表就绪，当前 {count} 条日志')
    except Exception as e:
        print(f'[logs] 警告: Logs 表初始化失败: {e}')

    # 注册路由
    app.register_blueprint(bp)
    routes = [
        'POST /log/ingest',
        'POST /log/query',
        'POST /log/stats',
        'POST /log/purge',
        'GET  /log/health',
    ]
    for r in routes:
        print(f'[logs] 路由注册: {r}')

    # 配置摘要
    print(f'[logs] 保留策略: {retention_days} 天 / 上限 {max_records} 条')
    print(f'[logs] 自动清理: 每 {cleanup_interval} 秒执行一次')

    # 启动后台清理线程
    _start_cleanup_thread(engine, metadata,
                          retention_days, max_records, cleanup_interval)
    print('[logs] 后台清理线程已启动')

    print('[logs] 日志模块初始化完成')


# ── 5.1 推送日志 ──────────────────────────────────────────────────────

@bp.route('/ingest', methods=['POST'])
def ingest():
    """接收外部日志推送（单条或批量）。"""
    data = request.get_json(silent=True)
    if not data or 'logs' not in data:
        return jsonify({'success': False, 'error': '请求体缺少 logs 字段'}), 400

    logs = data['logs']
    if not isinstance(logs, list) or not logs:
        return jsonify({'success': False, 'error': 'logs 必须是非空数组'}), 400
    if len(logs) > MAX_BATCH:
        return jsonify({
            'success': False,
            'error': f'单次最多 {MAX_BATCH} 条，当前 {len(logs)} 条'
        }), 400

    engine = app.config['LOG_ENGINE']
    metadata = app.config['LOG_METADATA']

    try:
        table = _ensure_logs_table(metadata, engine)
    except Exception as e:
        return jsonify({'success': False, 'error': f'表初始化失败: {e}'}), 500

    # 校验 + 组装
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, log in enumerate(logs):
        if not isinstance(log, dict):
            return jsonify({'success': False,
                            'error': f'第 {i} 条日志不是有效对象'}), 400

        service = log.get('service')
        level = log.get('level')
        message = log.get('message')

        if not service or service not in VALID_SERVICES:
            return jsonify({'success': False,
                            'error': f'第 {i} 条: service 无效或缺失'}), 400
        if not level or level not in VALID_LEVELS:
            return jsonify({'success': False,
                            'error': f'第 {i} 条: level 无效或缺失'}), 400
        if not message or not isinstance(message, str):
            return jsonify({'success': False,
                            'error': f'第 {i} 条: message 缺失或非字符串'}), 400

        if len(message) > MAX_MESSAGE_LEN:
            message = message[:MAX_MESSAGE_LEN]

        trace_id = log.get('trace_id')
        extra = log.get('extra')
        extra_json = None
        if extra is not None:
            extra_json = json.dumps(extra, ensure_ascii=False)
            if len(extra_json) > MAX_EXTRA_LEN:
                extra_json = extra_json[:MAX_EXTRA_LEN]

        rows.append({
            'ts': log.get('ts') or now,
            'service': service,
            'level': level,
            'message': message,
            'trace_id': trace_id,
            'extra': extra_json,
        })

    # 写入（成功后不再调 log_internal，防递归）
    try:
        with engine.connect() as conn:
            conn.execute(insert(table), rows)
            conn.commit()
    except Exception as e:
        # 失败时可以安全地记日志（log_internal 不回调 ingest）
        log_internal(engine, metadata, 'db', 'ERROR',
                     f'/log/ingest 写入失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': True, 'data': {'inserted': len(rows)}})


# ── 5.2 查询日志 ───────────────────────────────────────────────────────

@bp.route('/query', methods=['POST'])
def query():
    """结构化查询日志。"""
    data = request.get_json(silent=True) or {}

    engine = app.config['LOG_ENGINE']
    metadata = app.config['LOG_METADATA']

    try:
        table = _ensure_logs_table(metadata, engine)
    except Exception as e:
        return jsonify({'success': False, 'error': f'表初始化失败: {e}'}), 500

    clauses = []

    # service（支持单个字符串或数组）
    svc = data.get('service')
    if svc:
        col = table.columns['service']
        if isinstance(svc, list):
            clauses.append(col.in_(svc))
        else:
            clauses.append(col == svc)

    # level（支持单个字符串或数组）
    lvl = data.get('level')
    if lvl:
        col = table.columns['level']
        if isinstance(lvl, list):
            clauses.append(col.in_(lvl))
        else:
            clauses.append(col == lvl)

    # trace_id
    tid = data.get('trace_id')
    if tid:
        clauses.append(table.columns['trace_id'] == tid)

    # 时间区间
    since = data.get('since')
    until = data.get('until')
    ts_col = table.columns['ts']
    if since:
        clauses.append(ts_col >= since)
    if until:
        clauses.append(ts_col <= until)

    # 关键字模糊搜索
    keyword = data.get('keyword')
    if keyword:
        clauses.append(table.columns['message'].like(f'%{keyword}%'))

    where_cond = and_(*clauses) if clauses else None

    # 排序
    order_col_name = data.get('order_by', 'ts')
    if order_col_name not in ('id', 'ts', 'service', 'level'):
        order_col_name = 'ts'
    order_col = table.columns[order_col_name]
    desc = data.get('desc', True)
    order_expr = order_col.desc() if desc else order_col.asc()

    # 分页
    limit = min(data.get('limit', 100), 1000)
    offset = data.get('offset', 0)

    try:
        with engine.connect() as conn:
            # 总数
            count_stmt = select(func.count()).select_from(table)
            if where_cond is not None:
                count_stmt = count_stmt.where(where_cond)
            total = conn.execute(count_stmt).scalar() or 0

            # 查询
            stmt = select(table)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            stmt = stmt.order_by(order_expr).limit(limit).offset(offset)

            rows = conn.execute(stmt).fetchall()
            logs = []
            for row in rows:
                d = dict(row._mapping)
                if d.get('extra'):
                    try:
                        d['extra'] = json.loads(d['extra'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                logs.append(d)

        return jsonify({'success': True, 'data': {'total': total, 'logs': logs}})

    except Exception as e:
        log_internal(engine, metadata, 'db', 'ERROR',
                     f'/log/query 失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 5.3 统计聚合 ───────────────────────────────────────────────────────

@bp.route('/stats', methods=['POST'])
def stats():
    """日志分类统计。"""
    data = request.get_json(silent=True) or {}

    engine = app.config['LOG_ENGINE']
    metadata = app.config['LOG_METADATA']

    try:
        table = _ensure_logs_table(metadata, engine)
    except Exception as e:
        return jsonify({'success': False, 'error': f'表初始化失败: {e}'}), 500

    group_by = data.get('group_by', 'service')
    group_fields = [g.strip() for g in group_by.split(',') if g.strip()]
    for g in group_fields:
        if g not in ('service', 'level'):
            return jsonify({
                'success': False,
                'error': f'group_by 仅支持 service / level，收到: {g}'
            }), 400

    # 时间过滤
    clauses = []
    since = data.get('since')
    until = data.get('until')
    ts_col = table.columns['ts']
    if since:
        clauses.append(ts_col >= since)
    if until:
        clauses.append(ts_col <= until)
    where_cond = and_(*clauses) if clauses else None

    try:
        with engine.connect() as conn:
            cols = [table.columns[g] for g in group_fields] + [func.count().label('count')]
            stmt = select(*cols)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            stmt = stmt.group_by(*[table.columns[g] for g in group_fields])

            rows = conn.execute(stmt).fetchall()
            groups = [dict(row._mapping) for row in rows]

            # 总数
            count_stmt = select(func.count()).select_from(table)
            if where_cond is not None:
                count_stmt = count_stmt.where(where_cond)
            total = conn.execute(count_stmt).scalar() or 0

        return jsonify({'success': True, 'data': {'groups': groups, 'total': total}})

    except Exception as e:
        log_internal(engine, metadata, 'db', 'ERROR',
                     f'/log/stats 失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 5.4 清理过期 ──────────────────────────────────────────────────────

@bp.route('/purge', methods=['POST'])
def purge():
    """清理过期日志。"""
    data = request.get_json(silent=True) or {}
    before = data.get('before')
    if not before:
        return jsonify({'success': False, 'error': '缺少 before 字段'}), 400

    engine = app.config['LOG_ENGINE']
    metadata = app.config['LOG_METADATA']

    try:
        table = _ensure_logs_table(metadata, engine)
    except Exception as e:
        return jsonify({'success': False, 'error': f'表初始化失败: {e}'}), 500

    try:
        with engine.connect() as conn:
            stmt = delete(table).where(table.columns['ts'] < before)
            result = conn.execute(stmt)
            conn.commit()
        deleted = result.rowcount
        log_internal(engine, metadata, 'db', 'INFO',
                     f'/log/purge 清理 {deleted} 条 (before={before})')
        return jsonify({'success': True, 'data': {'deleted': deleted}})

    except Exception as e:
        log_internal(engine, metadata, 'db', 'ERROR',
                     f'/log/purge 失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 5.5 健康检查 ──────────────────────────────────────────────────────

@bp.route('/health', methods=['GET'])
def health():
    """日志模块健康检查。"""
    engine = app.config['LOG_ENGINE']
    metadata = app.config['LOG_METADATA']

    try:
        table = _ensure_logs_table(metadata, engine)
        with engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(table)).scalar() or 0
        return jsonify({'success': True, 'data': {'status': 'ok', 'count': count}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 后台自动清理线程 ──────────────────────────────────────────────────

def _start_cleanup_thread(engine, metadata,
                          retention_days=DEFAULT_RETENTION_DAYS,
                          max_records=DEFAULT_MAX_RECORDS,
                          interval=DEFAULT_CLEANUP_INTERVAL):
    """启动后台守护线程，定期清理过期日志。"""
    def _cleanup_loop():
        while True:
            time.sleep(interval)
            try:
                _do_cleanup(engine, metadata, retention_days, max_records)
            except Exception:
                # 清理失败不影响服务运行
                pass

    t = threading.Thread(target=_cleanup_loop, daemon=True, name='logs-cleanup')
    t.start()


def _do_cleanup(engine, metadata, retention_days, max_records):
    """执行一次清理：删过期 + 裁剪超量。"""
    table = _ensure_logs_table(metadata, engine)

    # 1. 删除过期日志
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with engine.connect() as conn:
        result = conn.execute(delete(table).where(table.columns['ts'] < cutoff))
        conn.commit()
        expired_deleted = result.rowcount

        # 2. 裁剪超量记录（保留最新的 max_records 条）
        count = conn.execute(select(func.count()).select_from(table)).scalar() or 0
        trimmed = 0
        if count > max_records:
            excess = count - max_records
            # 找到第 max_records 条的 ts（按时间正序的第 max_records 条）
            cutoff_row = conn.execute(
                select(table.columns['ts'])
                .order_by(table.columns['ts'].asc())
                .limit(1).offset(max_records)
            ).fetchone()
            if cutoff_row:
                trim_result = conn.execute(
                    delete(table).where(table.columns['ts'] < cutoff_row[0])
                )
                conn.commit()
                trimmed = trim_result.rowcount

    print(f'[logs] 自动清理完成: 过期删除 {expired_deleted} 条, 超量裁剪 {trimmed} 条')
    log_internal(engine, metadata, 'db', 'INFO',
                 f'自动清理: 过期删除 {expired_deleted} 条, 超量裁剪 {trimmed} 条')
