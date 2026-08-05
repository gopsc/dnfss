"""RpcClient / RpcServer（JSON RPC 约定层）单元测试。

纯标准库，直接运行：

    cd pymsg
    python -m unittest discover -s tests -v
"""

import json
import socket
import threading
import time
import unittest
from datetime import datetime, timezone

from pymsg import RpcClient, RpcError, RpcServer, send


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RpcFixture:
    """后台线程运行 RpcServer；routes: {name: fn}。"""

    def __init__(self, routes, workers=0, json_default=None):
        self.port = _free_port()
        self.server = RpcServer("127.0.0.1", self.port, workers=workers,
                                json_default=json_default)
        for name, fn in routes.items():
            self.server.add_route(name, fn)
        self.thread = threading.Thread(target=self.server.start, daemon=True)

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


class TestRpc(unittest.TestCase):

    def test_call_returns_data(self):
        with _RpcFixture({"echo": lambda req: {"got": req["msg"]}}) as fx:
            rpc = RpcClient("127.0.0.1", fx.port, "t")
            self.assertEqual(rpc.call("echo", {"msg": "hi"}), {"got": "hi"})

    def test_route_field_is_injected(self):
        seen = {}

        def handler(req):
            seen.update(req)
            return None

        with _RpcFixture({"r": handler}) as fx:
            RpcClient("127.0.0.1", fx.port).call("r", {"a": 1})
        # _route 已被服务端 pop，handler 只看到业务字段
        self.assertEqual(seen, {"a": 1})

    def test_business_error_raises_rpcerror(self):
        def handler(req):
            raise ValueError("bad input")

        with _RpcFixture({"x": handler}) as fx:
            with self.assertRaises(RpcError) as cm:
                RpcClient("127.0.0.1", fx.port).call("x")
            self.assertIn("bad input", str(cm.exception))

    def test_unknown_route_raises_rpcerror(self):
        with _RpcFixture({}) as fx:
            with self.assertRaises(RpcError) as cm:
                RpcClient("127.0.0.1", fx.port).call("nope")
            self.assertIn("未知路由", str(cm.exception))

    def test_malformed_json_gets_error_response(self):
        """服务端收到非法 JSON：返回 success=false 且不崩溃，后续请求正常。"""
        with _RpcFixture({"ok": lambda req: "fine"}) as fx:
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                send(s, "not-json{{{")
                from pymsg import recv
                resp = json.loads(recv(s, timeout=5))
            self.assertFalse(resp["success"])
            self.assertIn("invalid JSON request", resp["error"])
            # 服务仍然存活
            self.assertEqual(RpcClient("127.0.0.1", fx.port).call("ok"), "fine")

    def test_data_none_when_absent(self):
        with _RpcFixture({"n": lambda req: None}) as fx:
            self.assertIsNone(RpcClient("127.0.0.1", fx.port).call("n"))

    def test_json_default_serialization(self):
        def default(o):
            if isinstance(o, datetime):
                return o.isoformat()
            return str(o)

        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        with _RpcFixture({"t": lambda req: {"ts": now}}, json_default=default) as fx:
            data = RpcClient("127.0.0.1", fx.port).call("t")
            self.assertEqual(data["ts"], now.isoformat())

    def test_concurrent_rpc_with_workers(self):
        def handler(req):
            time.sleep(0.3)
            return req["i"] * 2

        with _RpcFixture({"dbl": handler}, workers=4) as fx:
            results = [None] * 6

            def w(i):
                results[i] = RpcClient("127.0.0.1", fx.port).call("dbl", {"i": i})

            t0 = time.monotonic()
            ts = [threading.Thread(target=w, args=(i,)) for i in range(6)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
            elapsed = time.monotonic() - t0

        self.assertEqual(results, [i * 2 for i in range(6)])
        self.assertLess(elapsed, 1.2, f"workers=4 应并发，实际 {elapsed:.2f}s")

    def test_error_path_is_redacted(self):
        """P1-3: 异常信息中的绝对路径应被脱敏，不透传内部细节。"""

        def handler(req):
            raise ValueError("open /opt/micro_servers/db/db_tools.py: no such file")

        with _RpcFixture({"boom": handler}) as fx:
            with self.assertRaises(RpcError) as cm:
                RpcClient("127.0.0.1", fx.port).call("boom")
            msg = str(cm.exception)
            self.assertIn("<redacted>", msg)
            self.assertNotIn("/opt/micro_servers", msg)

    def test_huge_integer_rejected(self):
        """P1-5: 超大整数应被拒绝，防止 CPU/内存 DoS。"""
        with _RpcFixture({"ok": lambda req: "fine"}) as fx:
            huge = "9" * 10000
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                send(s, '{"_route": "ok", "n": %s}' % huge)
                from pymsg import recv
                resp = json.loads(recv(s, timeout=5))
            self.assertFalse(resp["success"])

    def test_deeply_nested_json_rejected(self):
        """P1-5: 深度嵌套 JSON 应被拒绝，防止栈溢出。"""
        with _RpcFixture({"ok": lambda req: "fine"}) as fx:
            depth = 200000
            payload = "[" * depth + "]" * depth
            with socket.create_connection(("127.0.0.1", fx.port), timeout=5) as s:
                send(s, '{"_route": "ok", "n": %s}' % payload)
                from pymsg import recv
                resp = json.loads(recv(s, timeout=5))
            self.assertFalse(resp["success"])
            # 服务仍然存活
            self.assertEqual(RpcClient("127.0.0.1", fx.port).call("ok"), "fine")

    def test_per_call_timeout_override(self):
        with _RpcFixture({"slow": lambda req: time.sleep(2)}) as fx:
            rpc = RpcClient("127.0.0.1", fx.port, timeout=10.0)
            from pymsg import TimeoutError
            with self.assertRaises(TimeoutError):
                rpc.call("slow", timeout=0.3)


if __name__ == "__main__":
    unittest.main()
