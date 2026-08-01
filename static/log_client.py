"""
统一日志客户端 — 各服务共用（基于 pymsg.AsyncRpcQueue）。

设计要点：
  - 通过 pymsg RPC 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表
  - 异步非阻塞：内存队列 + 后台线程攒批推送，不拖慢业务
  - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
  - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
  - 时间戳：日志入队时即记录 ts（UTC ISO 格式），反映事件真实发生时间
  - 底层复用 pymsg.AsyncRpcQueue 通用队列基础设施

用法（与旧版完全兼容）：
    from log_client import logs

    logs.info("dnfs", "节点新增成功 id=42")
    logs.error("dnfs", "数据库连接失败", extra={"errno": 2002})
    logs.push("gateway", "WARNING", "登录失败次数超限",
              trace_id="req-abc", extra={"ip": "1.2.3.4"})
"""

import json
import os as _os
import sys as _sys
import atexit
from datetime import datetime, timezone

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
from pymsg import RpcClient, RpcError, AsyncRpcQueue  # noqa: E402

# ── 配置 ──────────────────────────────────────────────────────────────

_DBV2_ADDR = _os.environ.get('DB_HOST', '127.0.0.1')
_DBV2_PORT = int(_os.environ.get('DB_PORT', '8005'))

_BATCH_SIZE = 50
_FLUSH_INTERVAL = 2.0
_TIMEOUT = 5
_QUEUE_MAXSIZE = 10000


def _safe_json(obj):
    """安全序列化 extra 字段，不可序列化对象转为 repr 字符串。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return repr(obj)


def _now_iso():
    """生成当前 UTC 时间 ISO 字符串（入队时调用，保证时间戳准确）。"""
    return datetime.now(timezone.utc).isoformat()


class LogClient:
    """异步批量日志推送客户端（API 与旧版完全兼容）。"""

    def __init__(self, service_name: str = "log",
                 batch_size: int = _BATCH_SIZE,
                 flush_interval: float = _FLUSH_INTERVAL):
        self._service_name = service_name
        self._client = RpcClient(
            _DBV2_ADDR, _DBV2_PORT,
            service_name + "-log",
            timeout=_TIMEOUT,
        )
        self._queue = AsyncRpcQueue(
            handler=self._send_batch,
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_queue_size=_QUEUE_MAXSIZE,
            on_error=self._on_error,
        )
        self._queue.start()
        atexit.register(self.close)

    # ── 公开接口 ────────────────────────────────────────────────────────

    def push(self, service: str, level: str, message: str,
             trace_id: str = None, extra=None):
        """推送一条日志（非阻塞，立即返回）。

        队列满时静默丢弃最旧条目再放入新条目，保证不阻塞业务线程、不导致 OOM。
        """
        entry = {
            'ts': _now_iso(),
            'service': service,
            'level': level,
            'message': message,
            'trace_id': trace_id,
            'extra': extra,
        }
        self._queue.put(entry)

    def debug(self, service: str, message: str, **kw):
        self.push(service, 'DEBUG', message, **kw)

    def info(self, service: str, message: str, **kw):
        self.push(service, 'INFO', message, **kw)

    def warning(self, service: str, message: str, **kw):
        self.push(service, 'WARNING', message, **kw)

    def warn(self, service: str, message: str, **kw):
        self.push(service, 'WARNING', message, **kw)

    def error(self, service: str, message: str, **kw):
        self.push(service, 'ERROR', message, **kw)

    def critical(self, service: str, message: str, **kw):
        self.push(service, 'CRITICAL', message, **kw)

    def flush(self):
        """手动 flush 队列中剩余的日志。"""
        self._queue.flush()

    def close(self):
        """关闭客户端，flush 剩余日志。"""
        self._queue.close()

    # ── 内部实现 ────────────────────────────────────────────────────────

    def _send_batch(self, batch: list):
        """后台线程回调：逐条 INSERT 到 db（与旧版行为一致：网络错误放弃本批剩余）。"""
        for entry in batch:
            extra_val = entry.get("extra")
            try:
                extra_json = _safe_json(extra_val) if extra_val is not None else None
            except Exception:
                extra_json = None
            payload = {
                "action": "insert",
                "table": "Logs",
                "values": {
                    "ts": entry.get("ts", _now_iso()),
                    "service": entry.get("service"),
                    "level": entry.get("level"),
                    "message": entry.get("message"),
                    "trace_id": entry.get("trace_id"),
                    "extra": extra_json,
                },
            }
            try:
                self._client.call("execute", payload, timeout=_TIMEOUT)
            except RpcError as e:
                print(f"[log_client] INSERT 失败: {e}", file=_sys.stderr)
            except Exception as e:
                print(f"[log_client] 发送失败（放弃本批剩余）: {e}", file=_sys.stderr)
                break

    def _on_error(self, e: Exception, batch: list):
        """异常安全网：handler 外的异常也不会崩溃线程。"""
        print(f"[log_client] _send_batch 异常（已捕获，线程继续运行）: {e}",
              file=_sys.stderr)


# ── 全局单例 ──────────────────────────────────────────────────────────
# 各服务的 log_client.py 副本需将 service_name 改为自己的服务名。
# 此文件（confs/log_client.py）的 service_name 为 "confs"。

logs = LogClient(service_name="static")
