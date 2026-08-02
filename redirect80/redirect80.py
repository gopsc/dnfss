"""
redirect80 — HTTP 80 端口重定向服务。

监听 80 端口，所有 HTTP 请求返回 301/308 重定向到 HTTPS (443)，
保留原始 Host 和路径。为搜索引擎和浏览器提供 HTTP→HTTPS 无缝升级。

技术选型：Python 内置 http.server + 多线程混合模式，无外部 Web 框架依赖。
          日志通过 log_client 异步推送到 db 的 Logs 表（fail-safe）。

配置（两层加载）：
  1. confs 强依赖拉取 redirect80.ini（retry_on_refused 阻塞等待）
  2. 环境变量覆盖（Docker/k8s 兼容）
confs 不可用时直接退出，不做本地回退。
"""

import configparser
import os
import sys as _sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ── 路径设置 ──────────────────────────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, os.path.join(_here, ".."))
from log_client import logs  # noqa: E402

# ── 配置加载（confs 强依赖 → 环境变量覆盖）────────────────────────
_config = {
    "host": "0.0.0.0",
    "port": 80,
    "redirect_port": 443,
    "redirect_code": 301,
}

# 1. confs 强依赖拉取配置（阻塞等待 confs 就绪，失败则退出）
_sys.path.insert(0, os.path.join(_here, "..", "pymsg"))
from pymsg import RpcClient  # noqa: E402

_confs_host = os.environ.get("CONFS_HOST", "127.0.0.1")
_confs_port = int(os.environ.get("CONFS_PORT", "8010"))
_confs_client = RpcClient(
    _confs_host, _confs_port, "redirect80",
    retry_on_refused=True, timeout=5,
)
_ini_text = _confs_client.call("get", {"name": "redirect80.ini"})
_ini = configparser.ConfigParser(interpolation=None)
_ini.read_string(_ini_text)

_merge_map = {
    ("server", "host"): "host",
    ("server", "port"): "port",
    ("server", "redirect_port"): "redirect_port",
    ("server", "redirect_code"): "redirect_code",
}
for (sec, key), var in _merge_map.items():
    if _ini.has_option(sec, key):
        val = _ini.get(sec, key)
        _config[var] = type(_config[var])(val)
logs.info("redirect80", "配置已从 confs 加载")

# 2. 环境变量最高优先级覆盖
_env_map = {
    "REDIRECT80_HOST": "host",
    "REDIRECT80_PORT": ("port", int),
    "REDIRECT80_REDIRECT_PORT": ("redirect_port", int),
    "REDIRECT80_REDIRECT_CODE": ("redirect_code", int),
}
for env_var, mapping in _env_map.items():
    val = os.environ.get(env_var)
    if val is not None:
        if isinstance(mapping, tuple):
            _config[mapping[0]] = mapping[1](val)
        else:
            _config[mapping] = val

# ── 请求处理器 ──────────────────────────────────────────────────────────


class RedirectHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：将所有请求重定向到 HTTPS。

    GET/HEAD → 301 (Moved Permanently)，搜索引擎友好的永久重定向。
    POST/PUT/DELETE/PATCH → 308 (Permanent Redirect)，保留请求方法和 body。
    """

    server_version = "UniServe/redirect80"
    sys_version = ""

    def _redirect(self, code: int):
        """构造 HTTPS 重定向 URL 并返回。"""
        host = self.headers.get("Host", "")
        # 剥离端口号（如果客户端带了 :80）
        hostname = host.split(":")[0] if host else _config["host"]
        target_port = _config["redirect_port"]
        # 标准 443 端口不显式写 :443
        if target_port == 443:
            location = f"https://{hostname}{self.path}"
        else:
            location = f"https://{hostname}:{target_port}{self.path}"

        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "max-age=31536000")
        self.end_headers()

        # 日志：仅记录摘要（不刷屏）
        logs.info("redirect80",
                   f"{code} {self.command} {host} → {location}")

    def do_GET(self):
        self._redirect(301)

    def do_HEAD(self):
        self._redirect(301)

    def do_POST(self):
        self._redirect(308)

    def do_PUT(self):
        self._redirect(308)

    def do_DELETE(self):
        self._redirect(308)

    def do_PATCH(self):
        self._redirect(308)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, PUT, DELETE, PATCH, OPTIONS")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format, *args):
        """抑制默认的 stderr 日志（已通过 log_client 记录）。"""
        pass


# ── 多线程 HTTP 服务器 ──────────────────────────────────────────────────


class ThreadingRedirectServer(ThreadingMixIn, HTTPServer):
    """多线程混合 HTTP 服务器，处理并发连接。

    ThreadingMixIn 为每个请求创建新线程，适合 redirect80
    这种请求处理极快（无 I/O 阻塞）的场景。
    """
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """覆盖默认错误处理，异常写入 log_client 而非 stderr。"""
        logs.error("redirect80",
                    f"处理 {client_address} 请求时异常",
                    extra={"client": str(client_address)})


def main():
    host = _config["host"]
    port = int(_config["port"])

    server = ThreadingRedirectServer((host, port), RedirectHandler)

    logs.info("redirect80",
              f"HTTP→HTTPS 重定向服务启动 "
              f"http://{host}:{port} → https://*:{_config['redirect_port']}")

    print(f"[redirect80] 监听 http://{host}:{port} "
          f"→ 重定向到 :{_config['redirect_port']}", file=_sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logs.info("redirect80", "收到 SIGINT，正在关闭...")
        server.shutdown()
        logs.info("redirect80", "服务已停止")


if __name__ == "__main__":
    main()
