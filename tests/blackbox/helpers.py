"""
micro_servers 黑盒测试 — 共享工具模块。

提供: HTTP 请求封装、断言工具、测试计数器、pymsg 协议客户端。
纯标准库实现，零外部依赖。
"""

import http.client
import json
import socket
import struct
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl


# ---------------------------------------------------------------------------
# 测试计数器
# ---------------------------------------------------------------------------

class Counter:
    """每个测试模块一个计数器实例，统计 passed/failed/skipped。"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self._failures = []

    def add_pass(self):
        self.passed += 1

    def add_fail(self, name: str, reason: str = ""):
        self.failed += 1
        self._failures.append((name, reason))

    def add_skip(self):
        self.skipped += 1

    @property
    def total(self):
        return self.passed + self.failed + self.skipped

    def summary(self) -> str:
        return f"{self.total} tests: {self.passed} passed, {self.failed} failed, {self.skipped} skipped"

    def failures(self) -> list:
        return self._failures


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------

def assert_eq(actual, expected, name: str, counter: Counter):
    """断言 actual == expected。"""
    if actual == expected:
        print(f"  [PASS] {name}")
        counter.add_pass()
        return True
    else:
        print(f"  [FAIL] {name}: expected {expected!r}, got {actual!r}")
        counter.add_fail(name, f"expected {expected!r}, got {actual!r}")
        return False


def assert_contains(haystack: str, needle: str, name: str, counter: Counter):
    """断言 needle in haystack（不区分大小写）。"""
    if needle.lower() in haystack.lower():
        print(f"  [PASS] {name}")
        counter.add_pass()
        return True
    else:
        snippet = haystack[:200] if len(haystack) > 200 else haystack
        print(f"  [FAIL] {name}: '{needle}' not found in response")
        counter.add_fail(name, f"'{needle}' not found in response")
        return False


def assert_status(status: int, expected: int, name: str, counter: Counter):
    """断言 HTTP 状态码。"""
    if status == expected:
        print(f"  [PASS] {name}")
        counter.add_pass()
        return True
    else:
        print(f"  [FAIL] {name}: expected status {expected}, got {status}")
        counter.add_fail(name, f"expected status {expected}, got {status}")
        return False


def assert_true(condition: bool, name: str, counter: Counter, detail: str = ""):
    """断言条件为真。"""
    if condition:
        print(f"  [PASS] {name}")
        counter.add_pass()
        return True
    else:
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f": {detail}"
        print(msg)
        counter.add_fail(name, detail)
        return False


def assert_json_key(data: dict, key: str, name: str, counter: Counter):
    """断言 JSON 对象包含指定 key。"""
    if key in data:
        print(f"  [PASS] {name}")
        counter.add_pass()
        return True
    else:
        print(f"  [FAIL] {name}: key '{key}' not found in {json.dumps(data)[:200]}")
        counter.add_fail(name, f"key '{key}' not found")
        return False


def skip(name: str, reason: str, counter: Counter):
    """标记测试为跳过。"""
    print(f"  [SKIP] {name}: {reason}")
    counter.add_skip()


# ---------------------------------------------------------------------------
# HTTP 请求封装
# ---------------------------------------------------------------------------

def http_request(method: str, url: str, *,
                 body: dict = None,
                 headers: dict = None,
                 timeout: float = 10.0,
                 raw_body: bytes = None,
                 raw_body_content_type: str = None):
    """发送 HTTP 请求，返回 (status, body_str, resp_headers)。

    支持:
    - JSON body (body 参数)
    - 原始二进制 body (raw_body + raw_body_content_type)
    - 任意自定义 headers
    - 超时控制
    """
    parsed = urllib.parse.urlparse(url)
    data = None
    hdrs = {}

    if headers:
        hdrs.update(headers)

    if raw_body is not None:
        data = raw_body
        if raw_body_content_type:
            hdrs["Content-Type"] = raw_body_content_type
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    ctx = ssl._create_unverified_context() if parsed.scheme == "https" else None

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            resp_headers = dict(resp.headers)
            return resp.status, resp_body, resp_headers
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        resp_headers = dict(e.headers) if e.headers else {}
        return e.code, resp_body, resp_headers
    except Exception as e:
        return 0, str(e), {}


def http_get(url: str, headers: dict = None, timeout: float = 10.0):
    """HTTP GET 请求。"""
    return http_request("GET", url, headers=headers, timeout=timeout)


def http_post(url: str, body: dict = None, headers: dict = None,
              timeout: float = 10.0):
    """HTTP POST 请求（JSON body）。"""
    return http_request("POST", url, body=body, headers=headers, timeout=timeout)


def http_post_raw(url: str, raw_body: bytes, content_type: str,
                  headers: dict = None, timeout: float = 10.0):
    """HTTP POST 请求（原始二进制 body）。"""
    return http_request("POST", url, raw_body=raw_body,
                        raw_body_content_type=content_type,
                        headers=headers, timeout=timeout)


def try_parse_json(text: str):
    """尝试解析 JSON，失败返回 None。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# pymsg 协议客户端
# ---------------------------------------------------------------------------

class PymsgClient:
    """pymsg TCP 协议客户端（用于直接与 db:8005 通信）。

    协议格式: [4字节大端长度][UTF-8 JSON payload]
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8005,
                 name: str = "test_client"):
        self.host = host
        self.port = port
        self.name = name
        self._sock = None

    def _ensure_connected(self):
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self.host, self.port))

    def _close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send_frame(self, data: str):
        """发送一帧 pymsg 数据。"""
        payload = data.encode("utf-8")
        header = struct.pack(">I", len(payload))
        self._sock.sendall(header + payload)

    def _recv_frame(self, timeout: float = 10.0) -> str:
        """接收一帧 pymsg 数据。"""
        self._sock.settimeout(timeout)
        header = self._recv_exact(4, timeout)
        if header is None:
            raise ConnectionError("Failed to receive pymsg header")
        length = struct.unpack(">I", header)[0]
        payload = self._recv_exact(length, timeout)
        if payload is None:
            raise ConnectionError("Failed to receive pymsg payload")
        return payload.decode("utf-8")

    def _recv_exact(self, n: int, timeout: float = 10.0):
        """精确接收 n 字节。"""
        self._sock.settimeout(timeout)
        data = b""
        while len(data) < n:
            try:
                chunk = self._sock.recv(n - len(data))
            except socket.timeout:
                return None
            if not chunk:
                return None
            data += chunk
        return data

    def call(self, payload: dict, timeout: float = 10.0) -> dict:
        """发送请求并接收响应（短连接模式）。

        payload 必须包含 "_route": "execute"。
        返回 {"success": true/false, "data": [...], "error": "..."}
        """
        try:
            self._ensure_connected()
            msg = json.dumps(payload)
            self._send_frame(msg)
            resp_text = self._recv_frame(timeout)
            return json.loads(resp_text)
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._close()


# ---------------------------------------------------------------------------
# 服务可用性检查
# ---------------------------------------------------------------------------

def check_tcp_service(host: str, port: int, timeout: float = 5.0) -> tuple:
    """检查纯 TCP 服务（如 pymsg 协议服务 db/captcha/smtp）是否可达。
    返回 (reachable: bool, detail: str)。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "TCP 连接成功"
    except ConnectionRefusedError:
        return False, "连接被拒绝 (服务未启动或端口未监听)"
    except socket.timeout:
        return False, f"TCP 连接超时 ({timeout}s)"
    except OSError as e:
        return False, f"网络错误: {e}"


def check_service(url: str, timeout: float = 8.0) -> tuple:
    """检查服务是否可达（HTTP 请求检测，与测试实际行为一致）。
    返回 (reachable: bool, detail: str)。
    任何 HTTP 响应（包括 200/302/401/403 等）均视为可达。
    """
    parsed = urllib.parse.urlparse(url)
    ctx = ssl._create_unverified_context() if parsed.scheme == "https" else None
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # 任何 HTTP 错误码都说明服务在运行
        return True, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        return False, reason
    except socket.timeout:
        return False, f"HTTP 请求超时 ({timeout}s)"
    except ConnectionRefusedError:
        return False, "连接被拒绝 (服务未启动或端口未监听)"
    except socket.gaierror:
        return False, "DNS 解析失败"
    except OSError as e:
        return False, f"网络错误: {e.strerror if hasattr(e, 'strerror') else str(e)}"
    except Exception as e:
        return False, str(e)
