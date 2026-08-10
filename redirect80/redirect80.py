"""
redirect80 — HTTP 80 端口重定向服务。

监听 80 端口，所有 HTTP 请求返回 301/308 重定向到 HTTPS (443)，
保留原始 Host 和路径。为搜索引擎和浏览器提供 HTTP→HTTPS 无缝升级。

技术选型：Python 内置 http.server + 多线程混合模式，无外部 Web 框架依赖。
          日志通过 log_client 异步推送到 db 的 Logs 表（fail-safe）。

配置（三层加载）：
  1. 代码默认值
  2. 环境变量覆盖（REDIRECT80_* 前缀，Docker/k8s 兼容）
  3. 命令行参数（最高优先级，由 core/services.yaml 传入）
"""

import argparse
import os
import sys as _sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ── 路径设置 ──────────────────────────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, os.path.join(_here, ".."))
from log_client import logs, set_log_level  # noqa: E402


# ── 配置加载 ─────────────────────────────────────────────────────────

def load_config(argv):
    """三层加载: 默认值 → ENV → CLI（最高优先级）。"""

    # argparse 先行：--help 在任何网络调用前处理
    parser = argparse.ArgumentParser(
        prog="redirect80",
        description="HTTP 80 端口重定向服务",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="配置优先级: CLI > 环境变量 > 默认值",
    )
    parser.add_argument("--host", default=None,
                        help="绑定地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口，默认 80")
    parser.add_argument("--redirect-port", type=int, default=None,
                        help="重定向目标端口，默认 443")
    parser.add_argument("--redirect-code", type=int, default=None,
                        help="GET/HEAD 重定向状态码，默认 301")
    parser.add_argument("--log-level", default=None,
                        help="debug|info|warn|error，默认 info")
    parser.add_argument("--instance", default=None,
                        help="实例名（注册中心用），默认 redirect80")
    parser.add_argument("--help", "-h", action="help",
                        help="显示此帮助并退出")
    args = parser.parse_args(argv)

    # 默认值
    config = {
        "host": "0.0.0.0",
        "port": 80,
        "redirect_port": 443,
        "redirect_code": 301,
        "log_level": "info",
        "instance": "redirect80",
    }

    # 1. 环境变量覆盖
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
                config[mapping[0]] = mapping[1](val)
            else:
                config[mapping] = val

    # 2. CLI 参数（最高优先级）
    if args.host is not None:
        config["host"] = args.host
    if args.port is not None:
        config["port"] = args.port
    if args.redirect_port is not None:
        config["redirect_port"] = args.redirect_port
    if args.redirect_code is not None:
        config["redirect_code"] = args.redirect_code
    if args.log_level is not None:
        config["log_level"] = args.log_level
    if args.instance is not None:
        config["instance"] = args.instance

    # log_level 合法性校验
    lv = config["log_level"].lower()
    if lv == "warning":
        lv = "warn"
    if lv not in ("debug", "info", "warn", "error"):
        print(f"[redirect80] invalid log_level: {config['log_level']} "
              f"(must be debug|info|warn|error)", file=_sys.stderr)
        _sys.exit(1)
    config["log_level"] = lv

    return config


# ── 模块级配置（main() 中赋值）──────────────────────────────────────
_config = {}


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
        self._redirect(_config["redirect_code"])

    def do_HEAD(self):
        self._redirect(_config["redirect_code"])

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
    global _config
    _config = load_config(_sys.argv[1:])

    # 设置日志级别过滤
    set_log_level(_config["log_level"])

    host = _config["host"]
    port = int(_config["port"])

    server = ThreadingRedirectServer((host, port), RedirectHandler)

    logs.info("redirect80",
              f"服务启动 instance={_config['instance']} "
              f"http://{host}:{port} → https://*:{_config['redirect_port']}")

    print(f"[redirect80] instance={_config['instance']} log-level={_config['log_level']} "
          f"监听 http://{host}:{port} → 重定向到 :{_config['redirect_port']}",
          file=_sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logs.info("redirect80", "收到 SIGINT，正在关闭...")
        server.shutdown()
        logs.info("redirect80", "服务已停止")


if __name__ == "__main__":
    main()
