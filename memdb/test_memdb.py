"""
test_memdb.py — memdb RPC 服务端集成测试（pymsg 协议）

基于 pymsg TCP 协议，直接与 memdb:8011 通信。
测试所有 9 个 RPC 路由 + 并发安全性。

用法: python test_memdb.py [--host 127.0.0.1] [--port 8011]
"""

import json
import socket
import struct
import sys
import threading
import time


class MemdbClient:
    """memdb pymsg 协议客户端（短连接模式）。"""

    def __init__(self, host="127.0.0.1", port=8011, timeout=5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def call(self, route, **params):
        """发送 JSON-RPC 请求，返回 (success, data)。"""
        payload = {"_route": route}
        payload.update(params)
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            body = json.dumps(payload).encode("utf-8")
            sock.sendall(struct.pack(">I", len(body)) + body)

            header = self._recv_exact(sock, 4)
            if header is None:
                return False, "no response header"
            length = struct.unpack(">I", header)[0]
            payload_bytes = self._recv_exact(sock, length)
            if payload_bytes is None:
                return False, "incomplete payload"
            resp = json.loads(payload_bytes.decode("utf-8"))
            return resp.get("success", False), resp.get("data", resp.get("error"))
        except Exception as e:
            return False, str(e)
        finally:
            if sock:
                sock.close()

    @staticmethod
    def _recv_exact(sock, n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data


# ── 测试框架 ────────────────────────────────────────────────────────────

class Counter:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, condition, name, detail=""):
        if condition:
            print(f"  [PASS] {name}")
            self.passed += 1
            return True
        else:
            msg = f"  [FAIL] {name}"
            if detail:
                msg += f": {detail}"
            print(msg)
            self.failed += 1
            self.failures.append((name, detail))
            return False

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"Results: {self.passed} passed, {self.failed} failed ({total} total)")
        if self.failures:
            print("Failures:")
            for name, detail in self.failures:
                print(f"  - {name}: {detail}")
        return self.failed == 0


# ── 测试用例 ────────────────────────────────────────────────────────────

def test_ping(c, cnt):
    ok, data = c.call("ping")
    cnt.check(ok and data == "pong", "ping", f"got {data}")


def test_set_and_get(c, cnt):
    ok, data = c.call("set", key="test:key1", value="hello")
    cnt.check(ok and data is True, "set new key", f"got {data}")

    ok, data = c.call("get", key="test:key1")
    cnt.check(ok and data == "hello", "get existing key", f"got {data}")

    ok, data = c.call("get", key="test:nonexistent")
    cnt.check(ok and data is None, "get nonexistent key", f"got {data}")


def test_set_duplicate(c, cnt):
    c.call("set", key="test:dup", value="first")
    ok, data = c.call("set", key="test:dup", value="second")
    cnt.check(ok and data is False, "set duplicate key", f"got {data}")

    ok, data = c.call("get", key="test:dup")
    cnt.check(ok and data == "first", "value unchanged after duplicate set", f"got {data}")


def test_del(c, cnt):
    c.call("set", key="test:del", value="tmp")
    ok, data = c.call("del", key="test:del")
    cnt.check(ok and data is True, "del existing key", f"got {data}")

    ok, data = c.call("get", key="test:del")
    cnt.check(ok and data is None, "get after del returns null", f"got {data}")

    ok, data = c.call("del", key="test:del")
    cnt.check(ok and data is False, "del nonexistent key", f"got {data}")


def test_update(c, cnt):
    c.call("set", key="test:update", value="v1")
    ok, data = c.call("update", key="test:update", value="v2")
    cnt.check(ok and data is True, "update without refresh", f"got {data}")

    ok, data = c.call("get", key="test:update")
    cnt.check(ok and data == "v2", "value after update", f"got {data}")

    ok, data = c.call("update", key="test:nonexistent", value="x")
    cnt.check(ok and data is False, "update nonexistent", f"got {data}")


def test_update_with_refresh(c, cnt):
    c.call("set", key="test:refresh", value="old")
    ok, data = c.call("update", key="test:refresh", value="new", refresh_ttl=True)
    cnt.check(ok and data is True, "update with refresh_ttl", f"got {data}")

    ok, data = c.call("get", key="test:refresh")
    cnt.check(ok and data == "new", "value after refresh update", f"got {data}")


def test_exists(c, cnt):
    c.call("set", key="test:exists", value="yes")
    ok, data = c.call("exists", key="test:exists")
    cnt.check(ok and data is True, "exists true", f"got {data}")

    ok, data = c.call("exists", key="test:ghost")
    cnt.check(ok and data is False, "exists false", f"got {data}")


def test_size(c, cnt):
    # 清理
    for k in ["test:s1", "test:s2", "test:s3"]:
        c.call("del", key=k)

    c.call("set", key="test:s1", value="a")
    c.call("set", key="test:s2", value="b")
    ok, data = c.call("size")
    cnt.check(ok and isinstance(data, int) and data >= 2, "size >= 2", f"got {data}")

    c.call("del", key="test:s1")
    ok, data = c.call("size")
    cnt.check(ok and isinstance(data, int), "size after del", f"got {data}")


def test_show(c, cnt):
    c.call("set", key="test:show1", value="v1")
    c.call("set", key="test:show2", value="v2")
    ok, data = c.call("show")
    cnt.check(ok and isinstance(data, list), "show returns array", f"type={type(data)}")
    if isinstance(data, list):
        keys = {item["key"] for item in data if "key" in item}
        cnt.check("test:show1" in keys, "show contains key1")
        cnt.check("test:show2" in keys, "show contains key2")


def test_expire(c, cnt):
    c.call("set", key="test:exp", value="old")
    ok, data = c.call("expire", key="test:exp", value="new")
    cnt.check(ok and data is True, "expire existing key", f"got {data}")

    ok, data = c.call("get", key="test:exp")
    cnt.check(ok and data == "new", "value after expire", f"got {data}")


def test_missing_params(c, cnt):
    ok, data = c.call("set", key="only_key")
    cnt.check(not ok and isinstance(data, str), "set missing 'value' returns error", f"got {data}")

    ok, data = c.call("get")
    cnt.check(not ok, "get missing 'key' returns error", f"got {data}")


def test_concurrent(c, cnt):
    """并发 set/get：验证 mutex 保护不崩溃"""
    errors = []
    results = []

    def worker(worker_id):
        client = MemdbClient()
        for i in range(20):
            key = f"concurrent:w{worker_id}:k{i}"
            ok, _ = client.call("set", key=key, value=str(worker_id))
            if not ok:
                errors.append(f"w{worker_id} set failed")
            ok, val = client.call("get", key=key)
            if not ok or val != str(worker_id):
                errors.append(f"w{worker_id} get mismatch: {val}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cnt.check(len(errors) == 0, "concurrent 4x20 set/get", f"{len(errors)} errors")
    if errors:
        for e in errors[:3]:
            print(f"    {e}")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8011

    print(f"=== memdb RPC integration tests ({host}:{port}) ===")
    print()

    # 检查服务是否可达
    try:
        s = socket.create_connection((host, port), timeout=3.0)
        s.close()
    except Exception as e:
        print(f"[SKIP] memdb service not reachable at {host}:{port}: {e}")
        print("Start the service first: cd memdb && bash _run.sh")
        return 1

    c = MemdbClient(host, port)
    cnt = Counter()

    test_ping(c, cnt)
    test_set_and_get(c, cnt)
    test_set_duplicate(c, cnt)
    test_del(c, cnt)
    test_update(c, cnt)
    test_update_with_refresh(c, cnt)
    test_exists(c, cnt)
    test_size(c, cnt)
    test_show(c, cnt)
    test_expire(c, cnt)
    test_missing_params(c, cnt)
    test_concurrent(c, cnt)

    return 0 if cnt.summary() else 1


if __name__ == "__main__":
    sys.exit(main())