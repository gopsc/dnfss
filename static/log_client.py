"""
统一日志客户端 — 通过 pymsg TCP 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表。

设计要点：
  - 通过 pymsg TCP 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表
  - 异步非阻塞：内存队列 + 后台线程批量推送，不拖慢业务
  - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
  - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush

用法：
    from log_client import logs

    logs.info("static", "服务启动成功")
    logs.error("static", "文件系统未启用", extra={"path": "/data"})
"""

import json
import os as _os
import sys as _sys
import threading
import queue
import time
import atexit
from datetime import datetime, timezone

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
from pymsg import MsgClient  # noqa: E402

# ── 配置 ──────────────────────────────────────────────────────────────

_DBV2_ADDR = '127.0.0.1'
_DBV2_PORT = 8005

_BATCH_SIZE = 50         # 队列满 50 条时 flush
_FLUSH_INTERVAL = 2.0    # 超时 2 秒强制 flush
_TIMEOUT = 5             # socket 超时（秒）


class LogClient:
    """异步批量日志推送客户端。

    通过 MsgClient（pymsg TCP 协议）逐条 INSERT 到 dbv2 的 Logs 表。
    """

    def __init__(self, service_name="static",
                 batch_size=_BATCH_SIZE,
                 flush_interval=_FLUSH_INTERVAL):
        self._service_name = service_name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._flush_signal = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop,
                                        daemon=True, name='log-client')
        self._thread.start()
        atexit.register(self._shutdown)

    # ── 公开接口 ────────────────────────────────────────────────────────

    def push(self, service, level, message, trace_id=None, extra=None):
        """推送一条日志（非阻塞，立即返回）。"""
        self._queue.put({
            'service': service,
            'level': level,
            'message': message,
            'trace_id': trace_id,
            'extra': extra,
        })

    def debug(self, service, message, **kw):
        self.push(service, 'DEBUG', message, **kw)

    def info(self, service, message, **kw):
        self.push(service, 'INFO', message, **kw)

    def warning(self, service, message, **kw):
        self.push(service, 'WARNING', message, **kw)

    def error(self, service, message, **kw):
        self.push(service, 'ERROR', message, **kw)

    def critical(self, service, message, **kw):
        self.push(service, 'CRITICAL', message, **kw)

    def flush(self):
        """手动 flush 队列中剩余的日志（含后台线程持有的局部 batch）。"""
        self._flush_signal.set()  # 通知后台线程立即 flush 其局部 batch
        time.sleep(0.6)  # 等待后台线程响应
        batch = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
                if len(batch) >= self._batch_size:
                    self._send(batch)
                    batch = []
            except queue.Empty:
                break
        if batch:
            self._send(batch)
        self._flush_signal.clear()

    # ── 内部实现 ────────────────────────────────────────────────────────

    def _flush_loop(self):
        """后台线程：积攒到 batch_size 或超时 flush_interval 时发送。"""
        batch = []
        last_flush = time.monotonic()

        while not self._stop.is_set():
            timeout = self._flush_interval - (time.monotonic() - last_flush)
            chk_timeout = max(0.0, min(timeout, 0.5))
            try:
                item = self._queue.get(timeout=chk_timeout)
                batch.append(item)
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                pass

            should_flush = (
                len(batch) >= self._batch_size or
                self._flush_signal.is_set() or
                (batch and time.monotonic() - last_flush >= self._flush_interval)
            )
            if should_flush and batch:
                self._send(batch)
                batch = []
                last_flush = time.monotonic()

        # 退出前 flush 剩余
        if batch:
            self._send(batch)

    def _send(self, batch):
        """通过 pymsg 使用 execute 路由逐条 INSERT 到 dbv2 Logs 表。"""
        now = datetime.now(timezone.utc).isoformat()
        try:
            client = MsgClient(_DBV2_ADDR, _DBV2_PORT, self._service_name + "-log")
            for entry in batch:
                extra_val = entry.get("extra")
                payload = {
                    "_route": "execute",
                    "action": "insert",
                    "table": "Logs",
                    "values": {
                        "ts": now,
                        "service": entry.get("service"),
                        "level": entry.get("level"),
                        "message": entry.get("message"),
                        "trace_id": entry.get("trace_id"),
                        "extra": json.dumps(extra_val, ensure_ascii=False)
                        if extra_val is not None else None,
                    },
                }
                resp = client.call(json.dumps(payload), timeout=_TIMEOUT)
                resp_obj = json.loads(resp)
                if not resp_obj.get("success"):
                    import sys
                    print(f"[log_client] INSERT 失败: {resp_obj.get('error')}",
                          file=sys.stderr)
        except Exception as e:
            import sys
            print(f"[log_client] 发送失败: {e}", file=sys.stderr)

    def _shutdown(self):
        """进程退出时 flush 剩余日志。"""
        self._stop.set()
        self.flush()


# ── 全局单例 ──────────────────────────────────────────────────────────

logs = LogClient(service_name="static")
