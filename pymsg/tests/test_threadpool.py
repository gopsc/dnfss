"""MsgServer 线程池模式（workers > 0）与默认同步模式的单元测试。

纯标准库实现，直接运行：

    cd pymsg
    python -m unittest discover -s tests -v
"""

import socket
import threading
import time
import unittest
import unittest.mock

from pymsg import MsgClient, MsgServer, recv, send


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerFixture:
    """在后台线程运行 MsgServer；handler 签名 handler(server, sock, addr, text)。"""

    def __init__(self, handler, workers=0):
        self.port = _free_port()
        self.server = MsgServer("127.0.0.1", self.port, workers=workers)
        self.thread = threading.Thread(
            target=self.server.start,
            args=(lambda sock, addr, text: handler(self.server, sock, addr, text),),
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("server did not start in time")

    def __exit__(self, *exc):
        self.server.stop()
        self.thread.join(timeout=5)
        return False


def _parallel_calls(port, messages, timeout=10.0):
    """多线程并发 call，按 messages 顺序返回响应列表。"""
    results = [None] * len(messages)

    def worker(i, msg):
        results[i] = MsgClient("127.0.0.1", port, f"t{i}").call(msg, timeout=timeout)

    threads = [threading.Thread(target=worker, args=(i, m)) for i, m in enumerate(messages)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 5)
    return results


class TestSyncMode(unittest.TestCase):
    """workers=0（默认）：行为与旧版一致。"""

    def test_rpc_echo(self):
        def handler(server, sock, addr, text):
            server.reply(sock, f"echo:{text}")

        with _ServerFixture(handler, workers=0) as fx:
            client = MsgClient("127.0.0.1", fx.port, "t")
            self.assertEqual(client.call("hello", timeout=5), "echo:hello")

    def test_serial_baseline(self):
        """同步模式处理慢回调是串行的：6 × 0.4s 应超过 2.0s。"""
        def handler(server, sock, addr, text):
            time.sleep(0.4)
            server.reply(sock, "ok")

        with _ServerFixture(handler, workers=0) as fx:
            t0 = time.monotonic()
            results = _parallel_calls(fx.port, ["m"] * 6)
            elapsed = time.monotonic() - t0
        self.assertEqual(results, ["ok"] * 6)
        self.assertGreater(elapsed, 2.0, f"同步模式应为串行，实际 {elapsed:.2f}s")


class TestThreadPoolMode(unittest.TestCase):
    """workers>0：回调并发执行，同一连接保持串行。"""

    def test_concurrent_calls(self):
        """6 个并发 call、回调各 sleep 0.4s、4 个 worker：约 2 批 ≈ 0.8s。"""
        def handler(server, sock, addr, text):
            time.sleep(0.4)
            server.reply(sock, f"ok:{text}")

        with _ServerFixture(handler, workers=4) as fx:
            t0 = time.monotonic()
            results = _parallel_calls(fx.port, [f"m{i}" for i in range(6)])
            elapsed = time.monotonic() - t0
        self.assertEqual(results, [f"ok:m{i}" for i in range(6)])
        self.assertLess(elapsed, 1.8, f"线程池模式应并发，实际 {elapsed:.2f}s")

    def test_same_connection_serialized(self):
        """同一连接连发两条消息，回调执行区间不得重叠。"""
        intervals = []
        lock = threading.Lock()

        def handler(server, sock, addr, text):
            start = time.monotonic()
            time.sleep(0.2)
            end = time.monotonic()
            with lock:
                intervals.append((start, end))
            server.reply(sock, f"r:{text}")

        with _ServerFixture(handler, workers=4) as fx:
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                send(s, "first")
                send(s, "second")
                r1 = recv(s, timeout=5)
                r2 = recv(s, timeout=5)

        self.assertEqual((r1, r2), ("r:first", "r:second"))
        self.assertEqual(len(intervals), 2)
        (s1, e1), (s2, e2) = sorted(intervals)
        self.assertLessEqual(e1, s2, "同一连接的两次回调发生了并发执行")

    def test_large_frames_not_interleaved(self):
        """并发回复大载荷（100KB）时帧不得交错损坏。"""
        payload = "x" * 100_000

        def handler(server, sock, addr, text):
            time.sleep(0.01)  # 放大交错窗口
            server.reply(sock, f"{text}|{payload}")

        with _ServerFixture(handler, workers=4) as fx:
            messages = [f"client{i}" for i in range(8)]
            results = _parallel_calls(fx.port, messages)
        self.assertEqual(results, [f"client{i}|{payload}" for i in range(8)])

    def test_callback_exception_keeps_server_alive(self):
        """回调抛非连接异常：打印 traceback、移除该连接，服务整体存活。"""
        def handler(server, sock, addr, text):
            if text == "boom":
                raise ValueError("bad message")
            server.reply(sock, "alive")

        with _ServerFixture(handler, workers=2) as fx:
            with unittest.mock.patch("traceback.print_exc"):
                with self.assertRaises(ConnectionError):
                    MsgClient("127.0.0.1", fx.port, "t").call("boom", timeout=5)
            # 后续请求不受影响
            resp = MsgClient("127.0.0.1", fx.port, "t").call("ping", timeout=5)
            self.assertEqual(resp, "alive")


if __name__ == "__main__":
    unittest.main()
