"""消息大小上限（_MAX_MSG_SIZE）单元测试。

验证 recv() 拒绝超大长度头、send() 拒绝超大载荷，
防止恶意客户端通过 4 字节 header 声明超大长度导致 OOM。

    cd pymsg
    python -m unittest discover -s tests -v
"""

import socket
import struct
import threading
import time
import unittest
import unittest.mock

import pymsg
from pymsg import MsgServer, recv, send, _MAX_MSG_SIZE


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerFixture:
    """后台线程运行 MsgServer。"""

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


class TestMaxMsgSize(unittest.TestCase):

    def test_recv_rejects_oversized_header(self):
        """服务端 recv() 收到超大长度头时应断开连接，不分配内存。"""
        with _ServerFixture(lambda srv, sock, addr, text: srv.reply(sock, "ok")) as fx:
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                # 发送合法 header 但声明长度为 _MAX_MSG_SIZE + 1
                s.sendall(struct.pack("!I", _MAX_MSG_SIZE + 1))
                # 服务端应断开连接（ConnectionError -> _remove_client）
                # 客户端读到 EOF
                data = s.recv(1024)
                self.assertEqual(data, b"")

    def test_recv_rejects_max_uint32(self):
        """声明 0xFFFFFFFF（4 GiB）也应被拒绝。"""
        with _ServerFixture(lambda srv, sock, addr, text: srv.reply(sock, "ok")) as fx:
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                s.sendall(struct.pack("!I", 0xFFFFFFFF))
                data = s.recv(1024)
                self.assertEqual(data, b"")

    def test_server_survives_oversized_message(self):
        """拒绝超大消息后服务仍然存活，后续正常请求不受影响。"""
        from pymsg import MsgClient

        def handler(server, sock, addr, text):
            server.reply(sock, f"ok:{text}")

        with _ServerFixture(handler, workers=2) as fx:
            # 发送恶意超大 header
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                s.sendall(struct.pack("!I", _MAX_MSG_SIZE + 1))
                s.recv(1024)  # 等待服务端断开

            # 等待服务端清理连接
            time.sleep(0.2)

            # 后续正常 RPC 仍然工作
            resp = MsgClient("127.0.0.1", fx.port, "t").call("ping", timeout=5)
            self.assertEqual(resp, "ok:ping")

    def test_send_rejects_oversized_payload(self):
        """send() 对超大载荷应直接抛 ValueError，不触碰网络。"""
        # 用 mock 将上限临时降到 100 字节，避免分配 64 MB 内存
        with unittest.mock.patch.object(pymsg, "_MAX_MSG_SIZE", 100):
            with _ServerFixture(lambda srv, sock, addr, text: None) as fx:
                with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                    oversized = "x" * 101
                    with self.assertRaises(ValueError) as cm:
                        send(s, oversized)
                    self.assertIn("too large", str(cm.exception))

    def test_normal_messages_unaffected(self):
        """合法大小的消息不受影响。"""
        from pymsg import MsgClient

        def handler(server, sock, addr, text):
            server.reply(sock, f"echo:{text}")

        with _ServerFixture(handler) as fx:
            # 1 MB 消息（远小于 64 MiB 上限）
            big = "A" * (1024 * 1024)
            resp = MsgClient("127.0.0.1", fx.port, "t").call(big, timeout=10)
            self.assertEqual(resp, f"echo:{big}")


if __name__ == "__main__":
    unittest.main()
