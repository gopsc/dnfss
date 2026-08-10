"""统一日志客户端 — 各服务共用（dnfs / smtp / static / confs 四份副本内容一致，改动请同步）。

设计要点：
  - 通过 pymsg RPC 调用 dbv2 execute 路由，逐条 INSERT 到 Logs 表
  - 异步非阻塞：内存队列 + 后台线程批量推送，不拖慢业务
  - fail-safe：db 服务不可用时静默丢弃，绝不影响业务；后台线程任何异常都不会崩溃
  - 缓冲批量：队列满 50 条或超时 2 秒时自动 flush
  - 时间戳：日志入队时即记录 ts，反映事件真实发生时间

用法：
    from log_client import logs

    logs.info("dnfs", "节点新增成功 id=42")
    logs.error("dnfs", "数据库连接失败", extra={"errno": 2002})
    logs.push("gateway", "WARNING", "登录失败次数超限",
              trace_id="req-abc", extra={"ip": "1.2.3.4"})
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
from pymsg import RpcClient, RpcError  # noqa: E402

# ── 配置 ──────────────────────────────────────────────────────────────

_DBV2_ADDR = _os.environ.get('DB_HOST', '127.0.0.1')
_DBV2_PORT = int(_os.environ.get('DB_PORT', '8005'))

_BATCH_SIZE = 50         # 队列满 50 条时 flush
_FLUSH_INTERVAL = 2.0    # 超时 2 秒强制 flush
_TIMEOUT = 5             # socket 超时（秒）
_QUEUE_MAXSIZE = 10000   # 队列上限，防止后台线程异常时内存无限增长；满则丢弃最旧条目

# ── 日志级别过滤 ──
_LOG_LEVELS = {
    "debug": 0, "info": 1, "warn": 2, "warning": 2,
    "error": 3, "critical": 4,
}
_LOG_LEVEL_THRESHOLD = _LOG_LEVELS["info"]  # 默认 info


def _normalize_level(level):
    """归一化级别名：大写转小写，warning→warn。"""
    lv = (level or "info").lower()
    if lv == "warning":
        lv = "warn"
    return lv


def set_log_level(level):
    """设置日志过滤阈值（低于该级别的日志将被丢弃）。"""
    global _LOG_LEVEL_THRESHOLD
    lv = _normalize_level(level)
    if lv in _LOG_LEVELS:
        _LOG_LEVEL_THRESHOLD = _LOG_LEVELS[lv]


def _safe_json(obj):
    """安全序列化 extra 字段，不可序列化对象转为 repr 字符串，保证不抛异常。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return repr(obj)


def _now_iso():
    """生成当前 UTC 时间 ISO 字符串（在入队时调用，保证时间戳准确）。"""
    return datetime.now(timezone.utc).isoformat()


class LogClient:
    """异步批量日志推送客户端。"""

    def __init__(self, service_name="log",
                 batch_size=_BATCH_SIZE,
                 flush_interval=_FLUSH_INTERVAL):
        self._service_name = service_name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._stop = threading.Event()
        self._flush_signal = threading.Event()
        self._flush_done = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop,
                                        daemon=True, name='log-client')
        self._thread.start()
        atexit.register(self._shutdown)

    # ── 公开接口 ────────────────────────────────────────────────────────

    def push(self, service, level, message, trace_id=None, extra=None):
        """推送一条日志（非阻塞，立即返回）。

        队列满时静默丢弃最旧条目再放入新条目，保证不阻塞业务线程、不导致 OOM。
        """
        # 级别过滤：低于当前阈值的日志直接丢弃（不进队列）
        if _LOG_LEVELS.get(_normalize_level(level), 0) < _LOG_LEVEL_THRESHOLD:
            return
        entry = {
            'ts': _now_iso(),  # 入队时即生成时间戳，反映真实事件时间
            'service': service,
            'level': level,
            'message': message,
            'trace_id': trace_id,
            'extra': extra,
        }
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            # 队列满：丢弃最旧的一条，再放入新条目（drop-oldest 策略）
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(entry)
            except queue.Full:
                pass  # 极端竞争下仍失败则静默丢弃

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
        self._flush_done.clear()
        self._flush_signal.set()  # 通知后台线程立即 flush 其局部 batch
        # 等待后台线程完成 flush（最多等待 flush_interval + 2 秒余量）
        self._flush_done.wait(timeout=self._flush_interval + 2.0)
        # 主线程排空队列中剩余条目
        self._drain_queue_to_db()

    def _drain_queue_to_db(self):
        """排空主线程可见的队列中剩余条目，直接发送到 db。"""
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
        """后台线程：积攒到 batch_size 或超时 flush_interval 时发送。

        外层捕获所有异常，保证后台线程在任何情况下（网络故障、序列化错误等）
        都不会死亡，避免日志队列无限增长导致 OOM。
        """
        batch = []
        last_flush = time.monotonic()

        while not self._stop.is_set():
            try:
                timeout = self._flush_interval - (time.monotonic() - last_flush)
                chk_timeout = max(0.1, min(timeout, 0.5))
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
                    try:
                        self._send(batch)
                    except Exception as e:
                        # _send 内部已有细粒度异常处理，此处是最终安全网
                        print(f"[log_client] _send 异常（已捕获，线程继续运行）: {e}",
                              file=_sys.stderr)
                    batch = []
                    last_flush = time.monotonic()
                    if self._flush_signal.is_set():
                        self._flush_signal.clear()
                        self._flush_done.set()
            except Exception as e:
                # 最外层安全网：任何意外异常都不应杀死日志线程
                print(f"[log_client] _flush_loop 异常（已捕获，线程继续运行）: {e}",
                      file=_sys.stderr)
                time.sleep(0.5)  # 防止异常风暴导致 CPU 满载

        # 退出前 flush 剩余
        if batch:
            try:
                self._send(batch)
            except Exception as e:
                print(f"[log_client] 退出时 flush 异常: {e}", file=_sys.stderr)
        self._flush_done.set()

    def _send(self, batch):
        """通过 pymsg RPC（execute 路由）批量发送到 dbv2 Logs 表。

        整个 batch 复用一个 TCP 连接；网络错误时放弃本批剩余条目（fail-safe），
        不抛异常给调用方。
        """
        try:
            client = RpcClient(_DBV2_ADDR, _DBV2_PORT, self._service_name + "-log")
        except Exception as e:
            print(f"[log_client] 创建 RpcClient 失败: {e}", file=_sys.stderr)
            return

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
                client.call("execute", payload, timeout=_TIMEOUT)
            except RpcError as e:
                # 业务错误（如表结构不符）：打印后继续下一条
                print(f"[log_client] INSERT 失败: {e}", file=_sys.stderr)
            except Exception as e:
                # 网络错误：db 不可用，放弃本批剩余（fail-safe）
                print(f"[log_client] 发送失败: {e}", file=_sys.stderr)
                break

    def _shutdown(self):
        """进程退出时 flush 剩余日志。"""
        self._stop.set()
        self.flush()


# ── 全局单例 ──────────────────────────────────────────────────────────

logs = LogClient()
