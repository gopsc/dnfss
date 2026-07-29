"""
smtp — 基于 pymsg 协议（MsgServer）的邮箱验证码服务。

替代原 Flask HTTP 接口，通过 TCP 长度前缀帧收发 JSON。
协议：每条消息为完整 JSON，``_route`` 字段指定路由名。

请求示例::

    {"_route": "send_email", "email": "user@example.com"}          → 生成并发送验证码
    {"_route": "verify_email", "email": "...", "code": "123456"}   → 校验（一次性消费）
    {"_route": "health"}                                           → 健康检查

响应格式（与 dbv2 / captcha 一致）::

    {"success": true,  "data": {...}}
    {"success": false, "error": "..."}

SMTP 账号经 .env 配置：SMTP_SERVER / SMTP_PORT / SMTP_USER / __PASSWORD
（__PASSWORD 兼容别名 SMTP_PASSWORD）。
"""

import json
import os
import random
import smtplib
import threading
import time
from email.mime.text import MIMEText

# 手动加载 .env（支持 KEY=VALUE / KEY: VALUE / KEY VALUE / export KEY=... 格式，容忍空行/注释/引号）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_env_loaded = 0
if os.path.isfile(_env_path):
    with open(_env_path, "r") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if _line.startswith("export "):
                _line = _line[len("export "):].strip()
            # 支持 KEY=VALUE / KEY: VALUE / KEY VALUE 三种写法
            if "=" in _line:
                _key, _, _val = _line.partition("=")
            elif ":" in _line:
                _key, _, _val = _line.partition(":")
            else:
                _parts = _line.split(None, 1)
                _key = _parts[0]
                _val = _parts[1] if len(_parts) > 1 else ""
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val
                _env_loaded += 1
else:
    print(f"[smtp] 警告: 未找到 .env 文件: {_env_path}")

from log_client import logs

# pymsg 导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import MsgServer  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────

SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
# 历史 .env 使用 __PASSWORD，允许 SMTP_PASSWORD 作为别名
SMTP_PASSWORD = os.environ.get("__PASSWORD") or os.environ.get("SMTP_PASSWORD", "")

CODE_LEN = 6
CODE_TTL = 600           # 验证码有效期（秒）
# 多数邮箱服务商（163/QQ 等）强制 From 头与登录账号一致，故默认跟随 SMTP_USER；
# 仅当服务商允许自定义发件身份时才在 .env 里显式配 MAIL_FROM 覆盖
MAIL_FROM = os.environ.get("MAIL_FROM") or SMTP_USER
DEFAULT_SUBJECT = "DNFS 邮箱验证"
# 有效期文案由 CODE_TTL 派生，改 TTL 不用同步改这里（{{code}} 是 f-string 转义）
DEFAULT_CONTENT = f"DNFS: 您的邮箱验证码为 {{code}}，{CODE_TTL // 60} 分钟内有效。"

code_store = {}          # email -> {'code': str, 'expires_at': float}
STORE_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
# SmtpServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class SmtpServer:
    """基于 pymsg 的邮箱验证码服务，通过 TCP 长连接收发 JSON。"""

    def __init__(self, addr="127.0.0.1", port=8008):
        self.addr = addr
        self.port = port
        self._server = MsgServer(addr, port)

        # 路由表
        self._routes = {
            "send_email": self._handle_send_email,
            "verify_email": self._handle_verify_email,
            "health": self._handle_health,
        }

    # ── 启动 / 停止 ──────────────────────────────────────────────────
    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        if not all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD]):
            # 只报缺失的 key 名，绝不报值
            missing = [k for k, v in (("SMTP_SERVER", SMTP_SERVER),
                                      ("SMTP_USER", SMTP_USER),
                                      ("__PASSWORD", SMTP_PASSWORD)) if not v]
            logs.error("smtp", f"SMTP 配置缺失: {','.join(missing)}"
                               f"（.env 加载 {_env_loaded} 项），send_email 将始终失败")
            print(f"[smtp] 警告: SMTP 配置缺失: {','.join(missing)}（.env 加载 {_env_loaded} 项）")
        print(f"[smtp] 监听 tcp://{self.addr}:{self.port}")
        logs.info("smtp", f"开始监听 tcp://{self.addr}:{self.port}")
        self._server.start(self._dispatch)

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ── 消息调度 -------------------------------------------------------
    def _dispatch(self, sock, addr, text):
        """解析 JSON 中的 _route，分发给对应的 handler，并回复结果。"""
        try:
            req = json.loads(text)
        except json.JSONDecodeError as e:
            self._reply(sock, False, error=f"JSON 解析失败: {e}")
            return

        route = req.pop("_route", "")
        handler = self._routes.get(route)
        if handler is None:
            self._reply(sock, False, error=f"未知路由: {route}")
            return

        try:
            result = handler(req)
            self._reply(sock, True, data=result)
        except Exception as e:
            logs.error("smtp", f"路由 {route} 处理异常: {e}")
            self._reply(sock, False, error=str(e))

    def _reply(self, sock, success, data=None, error=None):
        resp = {"success": success}
        if data is not None:
            resp["data"] = data
        if error is not None:
            resp["error"] = error
        self._server.reply(sock, json.dumps(resp, ensure_ascii=False))

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理
    # ═══════════════════════════════════════════════════════════════════

    def _handle_send_email(self, req):
        """生成 6 位数字验证码，存入内存（60s），经 SMTP_SSL 发送邮件。

        可选字段 subject / content 覆盖默认标题与正文，content 中 {code}
        会被替换为验证码。
        """
        email = str(req.get("email", "")).strip()
        if not email or "@" not in email:
            return {"sent": False, "error": "invalid email"}

        if not all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD]):
            logs.error("smtp", f"{email}: SMTP 配置缺失，无法发送")
            return {"sent": False, "error": "SMTP 服务未配置"}

        code = "".join(str(random.randint(0, 9)) for _ in range(CODE_LEN))
        subject = str(req.get("subject") or DEFAULT_SUBJECT)
        content = str(req.get("content") or DEFAULT_CONTENT).replace("{code}", code)

        with STORE_LOCK:
            code_store[email] = {'code': code, 'expires_at': time.time() + CODE_TTL}
            # 惰性清理过期项
            now = time.time()
            expired = [k for k, v in code_store.items() if v['expires_at'] < now]
            for k in expired:
                del code_store[k]

        message = MIMEText(content, "plain", "UTF-8")
        message["From"] = MAIL_FROM
        message["To"] = email
        message["Subject"] = subject

        # server 作用域限定在 try 内；连接失败时不会误调 quit()
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, [email], message.as_string())
        except Exception as err:
            # 发送失败：作废刚写入的验证码，避免"码已存但邮件没到"的不可用状态
            with STORE_LOCK:
                code_store.pop(email, None)
            logs.error("smtp", f"{email}: 验证码发送失败({err})")
            return {"sent": False, "error": f"验证码发送失败：{err}"}

        logs.info("smtp", f"{email}: 验证码发送成功({code})")
        return {"sent": True, "ttl": CODE_TTL}

    def _handle_verify_email(self, req):
        """校验验证码，验证通过即一次性消费；不存在的 email 安全返回 invalid。"""
        email = str(req.get("email", "")).strip()
        # 兼容旧字段 input_captcha
        code = str(req.get("code") or req.get("input_captcha") or "").strip()
        if not email or not code:
            return {"valid": False, "error": "email and code required"}

        with STORE_LOCK:
            entry = code_store.get(email)
            if entry and entry['expires_at'] < time.time():
                del code_store[email]
                entry = None
            if entry and entry['code'] == code:
                # 验证通过：一次性消费，防重放
                del code_store[email]
                valid = True
            else:
                valid = False

        if valid:
            logs.info("smtp", f"{email}: 验证通过")
            return {"valid": True}
        logs.info("smtp", f"{email}: 验证失败")
        return {"valid": False, "error": "invalid or expired code"}

    def _handle_health(self, _req):
        return {"status": "ok", "service": "smtp"}


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="smtp — pymsg 邮箱验证码服务")
    parser.add_argument("--addr", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8008, help="监听端口")
    args = parser.parse_args()

    server = SmtpServer(args.addr, args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
