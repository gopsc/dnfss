"""
AsyncRpcQueue - 通用异步批量发送队列

提供内存队列 + 后台线程的异步批量处理机制，适用于日志上报、指标采集等
非关键路径场景。业务线程put操作非阻塞，后台线程攒批处理，失败时通过错误回调
通知，不影响主业务流程。

两种使用模式：

1. 简单RPC模式（指定client + method + serialize）：
    queue = AsyncRpcQueue(
        client=rpc_client,
        method='log_api.push',
        batch_size=50,
        flush_interval=2.0,
        serialize=lambda items: {'logs': json.dumps(items)},
    )

2. 自定义处理模式（指定handler回调）：
    def send_batch(batch):
        for item in batch:
            rpc_client.call('execute', item)
    queue = AsyncRpcQueue(
        handler=send_batch,
        batch_size=50,
        flush_interval=2.0,
    )
"""

import threading
import time
from collections import deque
from typing import Any, Callable, Optional


class AsyncRpcQueue:
    """通用异步批量发送队列"""

    def __init__(
        self,
        client: Any = None,
        method: str = None,
        *,
        handler: Optional[Callable[[list], None]] = None,
        batch_size: int = 100,
        flush_interval: float = 2.0,
        max_queue_size: int = 10000,
        serialize: Optional[Callable[[list], dict]] = None,
        on_error: Optional[Callable[[Exception, list], None]] = None,
        retry_on_refused: bool = False,
        retry_interval: float = 1.0,
    ):
        """
        初始化异步队列

        Args:
            client: RpcClient实例（RPC模式必填）
            method: RPC方法名（RPC模式必填）
            handler: 自定义批量处理回调，接收一批items列表。提供此参数时
                     忽略client/method/serialize/retry_on_refused。
            batch_size: 批量发送的阈值，达到此数量立即发送
            flush_interval: 定时flush间隔（秒）
            max_queue_size: 队列最大长度，超过则丢弃最旧数据
            serialize: 序列化函数，接收items列表返回RPC参数字典
                       默认: lambda items: {'items': items}
            on_error: 错误回调，接收(exception, batch_items)
            retry_on_refused: 是否在连接被拒绝时重试（RPC模式，透传给client.call）
            retry_interval: 重试间隔（秒）（RPC模式，透传给client.call）
        """
        if handler is None:
            if client is None or method is None:
                raise ValueError("必须提供 handler 或 (client + method)")
            self._handler = self._make_rpc_handler(
                client, method,
                serialize or (lambda items: {'items': items}),
                retry_on_refused, retry_interval,
            )
        else:
            self._handler = handler

        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_queue_size = max_queue_size
        self._on_error = on_error

        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._dropped_count = 0

    @staticmethod
    def _make_rpc_handler(client, method, serialize, retry_on_refused, retry_interval):
        """创建RPC模式的默认handler"""
        def handler(batch: list):
            params = serialize(batch)
            client.call(
                method, params,
                retry_on_refused=retry_on_refused,
                retry_interval=retry_interval,
            )
        return handler

    def start(self) -> None:
        """启动后台发送线程"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f'AsyncRpcQueue',
            daemon=True,
        )
        self._thread.start()

    def put(self, item: Any) -> bool:
        """
        放入一条数据到队列（非阻塞）

        Args:
            item: 要发送的数据项

        Returns:
            bool: True表示放入成功，False表示队列已关闭
        """
        if self._closed:
            return False
        with self._lock:
            if len(self._queue) >= self._max_queue_size:
                self._queue.popleft()
                self._dropped_count += 1
            self._queue.append(item)
        self._wakeup.set()
        return True

    def flush(self) -> None:
        """立即触发一次发送（会阻塞直到处理完成，主要用于关闭时）"""
        self._send_batch()

    def close(self) -> None:
        """关闭队列，flush剩余数据后停止后台线程"""
        if self._closed:
            return
        self._closed = True
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._send_batch()

    @property
    def pending_count(self) -> int:
        """当前队列中待发送的数据量"""
        with self._lock:
            return len(self._queue)

    @property
    def dropped_count(self) -> int:
        """因队列满而丢弃的数据量"""
        return self._dropped_count

    def _run(self) -> None:
        """后台线程主循环"""
        last_flush = time.time()
        while not self._closed:
            self._wakeup.wait(timeout=0.1)
            self._wakeup.clear()

            now = time.time()
            should_flush = False
            with self._lock:
                if len(self._queue) >= self._batch_size:
                    should_flush = True
                elif now - last_flush >= self._flush_interval and self._queue:
                    should_flush = True
            if should_flush:
                self._send_batch()
                last_flush = time.time()

    def _send_batch(self) -> None:
        """取一批数据处理"""
        with self._lock:
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()
        try:
            self._handler(batch)
        except Exception as e:
            if self._on_error:
                try:
                    self._on_error(e, batch)
                except Exception:
                    pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
