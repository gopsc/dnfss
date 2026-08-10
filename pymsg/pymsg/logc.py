"""
pymsg.logc — 统一集中式日志客户端（Python 版）

通过 pymsg RPC 调用 db execute 路由，逐条 INSERT 到 Logs 表。

设计要点：
  - 异步非阻塞：基于 pymsg.AsyncRpcQueue 内存队列 + 后台线程攒批推送
  - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
  - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
  - 背压保护：队列上限 10000 条，满时丢最旧日志，防止 OOM
  - 日志级别过滤：set_level() 设定阈值，低于阈值的日志直接丢弃
  - 底层复用 pymsg.AsyncRpcQueue 通用队列基础设施

用法：
    from pymsg.logc import LogClient, set_log_level

    logs = LogClient(service_name="redirect80")
    logs.info("redirect80", "服务已启动")
    logs.error("redirect80", "端口绑定失败", extra={"errno": 98})

    # 设置日志级别（main 启动时调用）
    set_log_level("warn")   # 只输出 warn/error/critical
"""

import atexit
import json
import os
import sys
from datetime import datetime, timezone

from pymsg import AsyncRpcQueue, RpcClient, RpcError

# ── 配置 ──────────────────────────────────────────────────────────────────────

_DBV2_ADDR = os.environ.get("DB_HOST", "127.0.0.1")
_DBV2_PORT = int(os.environ.get("DB_PORT", "8005"))

_BATCH_SIZE = 50
_FLUSH_INTERVAL = 2.0
_TIMEOUT = 5
_QUEUE_MAXSIZE = 10000

# ── 日志级别过滤 ──────────────────────────────────────────────────────────────

_LEVEL_ORDER = {
    "DEBUG": 0, "INFO": 1,
    "WARNING": 2, "WARN": 2,
    "ERROR": 3, "CRITICAL": 3,
}
_level_threshold = 1  # 默认 INFO


def set_log_level(level: str):
    """设置全局日志级别阈值。低于此级别的日志直接丢弃。"""
    global _level_threshold
    lv = level.upper()
    if lv == "WARNING":
        lv = "WARN"
    _level_threshold = _LEVEL_ORDER.get(lv, 1)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _safe_json(obj):
    """安全序列化 extra 字段，不可序列化对象转为 repr 字符串。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return repr(obj)


def _now_iso():
    """生成当前 UTC 时间 ISO 字符串（入队时调用，保证时间戳准确）。"""
    return datetime.now(timezone.utc).isoformat()


# ── LogClient 类 ──────────────────────────────────────────────────────────────

class LogClient:
    """异步批量日志推送客户端。

    API 与 redirect80/log_client.py、confs/log_client.py 等保持一致，
    各服务可直接替换为 ``from pymsg.logc import LogClient``。
    """

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

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def set_log_level(self, level: str):
        """设置日志过滤阈值（委托给模块级 set_log_level）。"""
        set_log_level(level)

    def push(self, service: str, level: str, message: str,
             trace_id: str = None, extra=None):
        """推送一条日志（非阻塞，立即返回）。

        低于设定级别的日志直接丢弃。
        队列满时静默丢弃最旧条目再放入新条目，保证不阻塞业务线程。
        """
        if _LEVEL_ORDER.get(level.upper(), 1) < _level_threshold:
            return
        entry = {
            "ts": _now_iso(),
            "service": service,
            "level": level,
            "message": message,
            "trace_id": trace_id,
            "extra": extra,
        }
        self._queue.put(entry)

    def debug(self, service: str, message: str, **kw):
        self.push(service, "DEBUG", message, **kw)

    def info(self, service: str, message: str, **kw):
        self.push(service, "INFO", message, **kw)

    def warning(self, service: str, message: str, **kw):
        self.push(service, "WARNING", message, **kw)

    def warn(self, service: str, message: str, **kw):
        self.push(service, "WARNING", message, **kw)

    def error(self, service: str, message: str, **kw):
        self.push(service, "ERROR", message, **kw)

    def critical(self, service: str, message: str, **kw):
        self.push(service, "CRITICAL", message, **kw)

    def flush(self):
        """手动 flush 队列中剩余的日志。"""
        self._queue.flush()

    def close(self):
        """关闭客户端，flush 剩余日志。"""
        self._queue.close()

    # ── 内部实现 ──────────────────────────────────────────────────────────

    def _send_batch(self, batch: list):
        """后台线程回调：逐条 INSERT 到 db（网络错误放弃本批剩余）。"""
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
                print(f"[logc] INSERT 失败: {e}", file=sys.stderr)
            except Exception as e:
                print(f"[logc] 发送失败（放弃本批剩余）: {e}", file=sys.stderr)
                break

    def _on_error(self, e: Exception, batch: list):
        """异常安全网：handler 外的异常也不会崩溃线程。"""
        print(f"[logc] _send_batch 异常（已捕获，线程继续运行）: {e}",
              file=sys.stderr)
