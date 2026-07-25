"""
统一日志客户端 — 各服务共用。

设计要点：
  - 使用 urllib.request（标准库，不引入 requests）
  - 异步非阻塞：内存队列 + 后台线程批量推送，不拖慢业务
  - fail-safe：db 服务不可用时静默丢弃，绝不影响业务
  - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush

用法：
    from log_client import logs

    logs.info("dnfs", "节点新增成功 id=42")
    logs.error("dnfs", "数据库连接失败", extra={"errno": 2002})
    logs.push("gateway", "WARNING", "登录失败次数超限",
              trace_id="req-abc", extra={"ip": "1.2.3.4"})
"""

import json
import threading
import queue
import time
import urllib.request
import urllib.error
import atexit

# ── 配置 ──────────────────────────────────────────────────────────────

DB_LOG_URL = 'http://127.0.0.1:8005/log/ingest'

_BATCH_SIZE = 50         # 队列满 50 条时 flush
_FLUSH_INTERVAL = 2.0    # 超时 2 秒强制 flush
_TIMEOUT = 5             # HTTP 请求超时（秒）


class LogClient:
    """异步批量日志推送客户端。"""

    def __init__(self, url=DB_LOG_URL, batch_size=_BATCH_SIZE,
                 flush_interval=_FLUSH_INTERVAL):
        self._url = url
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue = queue.Queue()
        self._stop = threading.Event()
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
        """手动 flush 队列中剩余的日志。"""
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

    # ── 内部实现 ────────────────────────────────────────────────────────

    def _flush_loop(self):
        """后台线程：积攒到 batch_size 或超时 flush_interval 时发送。"""
        batch = []
        last_flush = time.monotonic()

        while not self._stop.is_set():
            timeout = max(0, self._flush_interval - (time.monotonic() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
                batch.append(item)
                # 继续非阻塞地取，直到满 batch_size
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                pass  # 超时，没有新日志

            # 满了或超时了就发
            should_flush = (
                len(batch) >= self._batch_size or
                (batch and time.monotonic() - last_flush >= self._flush_interval)
            )
            if should_flush:
                self._send(batch)
                batch = []
                last_flush = time.monotonic()

        # 退出前 flush 剩余
        if batch:
            self._send(batch)

    def _send(self, batch):
        """发送一批日志到 db 服务。失败时静默丢弃。"""
        data = json.dumps({'logs': batch}).encode('utf-8')
        req = urllib.request.Request(
            self._url, data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                resp.read()  # 消费响应体
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            # fail-safe：日志服务不可用，静默丢弃，不影响业务
            pass
        except Exception:
            pass

    def _shutdown(self):
        """进程退出时 flush 剩余日志。"""
        self._stop.set()
        self.flush()


# ── 全局单例 ──────────────────────────────────────────────────────────

logs = LogClient()
