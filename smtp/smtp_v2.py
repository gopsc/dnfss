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
from pymsg import RpcServer  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────

SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
# 历史 .env 使用 __PASSWORD，允许 SMTP_PASSWORD 作为别名
SMTP_PASSWORD = os.environ.get("__PASSWORD") or os.environ.get("SMTP_PASSWORD", "")

CODE_LEN = 6
CODE_TTL = 600           # 验证码有效期（秒）
SEND_INTERVAL = 60       # 同一邮箱两次成功发送的最小间隔（秒）
MAX_VERIFY_ATTEMPTS = 5  # 同一验证码最大校验失败次数，达到即作废（防枚举 6 位数字码）

# ── 邮件轰炸防护（兜底:gateway 层已有 CAPTCHA + 邮箱级限流,此处为最后防线） ──
# 邮箱级双窗口:与 gateway 对齐,但作为 gateway 被绕过/重启时的兜底
EMAIL_HOURLY_LIMIT = int(os.environ.get("EMAIL_HOURLY_LIMIT", "3"))   # 单邮箱 3 次/小时
EMAIL_DAILY_LIMIT = int(os.environ.get("EMAIL_DAILY_LIMIT", "10"))    # 单邮箱 10 次/天
# 全局熔断:防止 smtp 账号被服务商封禁;正常注册流量通常 <10/小时,默认值已是 10 倍异常
SMTP_HOURLY_LIMIT = int(os.environ.get("SMTP_HOURLY_LIMIT", "100"))   # 全局 100 封/小时
# 多数邮箱服务商（163/QQ 等）强制 From 头与登录账号一致，故默认跟随 SMTP_USER；
# 仅当服务商允许自定义发件身份时才在 .env 里显式配 MAIL_FROM 覆盖
MAIL_FROM = os.environ.get("MAIL_FROM") or SMTP_USER
DEFAULT_SUBJECT = "DNFS 邮箱验证"
# 有效期文案由 CODE_TTL 派生，改 TTL 不用同步改这里（{{code}} 是 f-string 转义）
DEFAULT_CONTENT = f"DNFS: 您的邮箱验证码为 {{code}}，{CODE_TTL // 60} 分钟内有效。"

code_store = {}          # email -> {'code': str, 'expires_at': float, 'attempts': int}
send_history = {}        # email -> 上次成功发送的时间戳（发送冷却用）

# 邮件轰炸防护:邮箱级长期限流与全局熔断的发送日志。
# send_log[email] = {'hour': [ts,...], 'day': [ts,...]}  仅记录成功发送的时间戳。
# global_send_log = [ts,...]  全局成功发送时间戳(小时窗口)。
# 惰性清理:每次检查时剔除已滑出窗口的时间戳,避免内存无限增长。
send_log = {}            # email -> {'hour': [ts], 'day': [ts]}
global_send_log = []     # [ts]  全局小时窗口发送时间戳

STORE_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
# SmtpServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class SmtpServer:
    """基于 pymsg 的邮箱验证码服务，通过 TCP 长连接收发 JSON。"""

    def __init__(self, addr="127.0.0.1", port=8008, workers=4):
        self.addr = addr
        self.port = port
        self._server = RpcServer(
            addr, port, workers=workers,
            on_error=lambda route, e: logs.error(
                "smtp", f"路由 {route} 处理异常: {e}"),
        )
        self._server.add_route("send_email", self._handle_send_email)
        self._server.add_route("verify_email", self._handle_verify_email)
        self._server.add_route("health", self._handle_health)

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
        self._server.start()

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理
    # ═══════════════════════════════════════════════════════════════════

    def _handle_send_email(self, req):
        """生成 6 位数字验证码，存入内存（TTL 600s），经 SMTP_SSL 发送邮件。

        可选字段 subject / content 覆盖默认标题与正文，content 中 {code}
        会被替换为验证码。同一邮箱 SEND_INTERVAL 秒内仅允许成功发送一次，
        冷却期内的请求直接拒绝且不覆盖已发出的有效验证码。
        """
        email = str(req.get("email", "")).strip()
        if not email or "@" not in email:
            return {"sent": False, "error": "invalid email"}

        if not all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD]):
            logs.error("smtp", f"{email}: SMTP 配置缺失，无法发送")
            return {"sent": False, "error": "SMTP 服务未配置"}

        # 发送冷却：同一邮箱 SEND_INTERVAL 秒内仅放行一次；发送失败不计入，
        # 用户可立即重试
        with STORE_LOCK:
            remain = SEND_INTERVAL - (time.time() - send_history.get(email, 0.0))
            if remain > 0:
                wait = int(remain) + 1
                logs.info("smtp", f"{email}: 发送冷却中，剩余 {wait}s，已拒绝")
                return {"sent": False, "retry_after": wait,
                        "error": f"发送过于频繁，请 {wait} 秒后重试"}
            # 立即占位：防止并发请求同时通过冷却检查导致同邮箱重复发送；
            # 发送失败时在下方回滚，"失败不计入冷却"语义不变
            send_history[email] = time.time()

        # 邮件轰炸防护：邮箱级长期限流 + 全局熔断（预占模式，与 60s 冷却一致）
        # gateway 层已有 CAPTCHA + 邮箱级限流，此处为 gateway 被绕过/重启时的兜底。
        # 预占：检查时立即记录时间戳；发送失败时回滚，"失败不计入配额"语义不变。
        now = time.time()
        with STORE_LOCK:
            log = send_log.setdefault(email, {'hour': [], 'day': []})
            # 清理已滑出窗口的时间戳（惰性清理，避免内存无限增长）
            log['hour'] = [t for t in log['hour'] if now - t < 3600]
            log['day']  = [t for t in log['day']  if now - t < 86400]
            global_send_log[:] = [t for t in global_send_log if now - t < 3600]
            # 邮箱级配额检查
            if len(log['hour']) >= EMAIL_HOURLY_LIMIT:
                return {"sent": False,
                        "error": f"该邮箱每小时发送上限（{EMAIL_HOURLY_LIMIT}次）已达，请稍后重试"}
            if len(log['day']) >= EMAIL_DAILY_LIMIT:
                return {"sent": False,
                        "error": f"该邮箱每日发送上限（{EMAIL_DAILY_LIMIT}次）已达，请明日重试"}
            # 全局熔断：防 smtp 账号被服务商封禁
            if len(global_send_log) >= SMTP_HOURLY_LIMIT:
                logs.error("smtp", f"全局熔断：近 1 小时已发送 {len(global_send_log)} 封，"
                                   f"达到上限 {SMTP_HOURLY_LIMIT}，拒绝发送给 {email}")
                return {"sent": False,
                        "error": "邮件服务发送量已达上限，请稍后重试"}
            # 预占（发送失败时在下方回滚）
            log['hour'].append(now)
            log['day'].append(now)
            global_send_log.append(now)

        code = "".join(str(random.randint(0, 9)) for _ in range(CODE_LEN))
        subject = str(req.get("subject") or DEFAULT_SUBJECT)
        content = str(req.get("content") or DEFAULT_CONTENT).replace("{code}", code)

        with STORE_LOCK:
            code_store[email] = {'code': code,
                                 'expires_at': time.time() + CODE_TTL,
                                 'attempts': 0}
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
                send_history.pop(email, None)   # 回滚冷却占位，允许立即重试
                # 回滚限流预占（移除刚才追加的时间戳）
                log = send_log.get(email)
                if log:
                    if log['hour'] and log['hour'][-1] == now:
                        log['hour'].pop()
                    if log['day'] and log['day'][-1] == now:
                        log['day'].pop()
                if global_send_log and global_send_log[-1] == now:
                    global_send_log.pop()
            logs.error("smtp", f"{email}: 验证码发送失败({err})")
            return {"sent": False, "error": f"验证码发送失败：{err}"}

        # 发送成功：刷新冷却起点（检查阶段已占位），并惰性清理已失效的冷却记录
        with STORE_LOCK:
            send_history[email] = time.time()
            stale = [k for k, v in send_history.items()
                     if time.time() - v > SEND_INTERVAL]
            for k in stale:
                del send_history[k]

        logs.info("smtp", f"{email}: 验证码发送成功({code})")
        return {"sent": True, "ttl": CODE_TTL}

    def _handle_verify_email(self, req):
        """校验验证码，验证通过即一次性消费；不存在的 email 安全返回 invalid。

        同一验证码连续校验失败达 MAX_VERIFY_ATTEMPTS 次即作废删除（响应带
        invalidated 标记），防止对 6 位数字码的在线枚举。
        """
        email = str(req.get("email", "")).strip()
        # 兼容旧字段 input_captcha
        code = str(req.get("code") or req.get("input_captcha") or "").strip()
        if not email or not code:
            return {"valid": False, "error": "email and code required"}

        invalidated = False
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
                if entry:
                    # 仅对已发出且未过期的验证码计失败次数；达到上限即作废
                    entry['attempts'] = entry.get('attempts', 0) + 1
                    if entry['attempts'] >= MAX_VERIFY_ATTEMPTS:
                        del code_store[email]
                        invalidated = True

        if valid:
            logs.info("smtp", f"{email}: 验证通过")
            return {"valid": True}
        if invalidated:
            logs.info("smtp", f"{email}: 连续校验失败 {MAX_VERIFY_ATTEMPTS} 次，验证码已作废")
            return {"valid": False, "invalidated": True,
                    "error": f"验证码连续错误 {MAX_VERIFY_ATTEMPTS} 次，已失效，请重新获取"}
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
    parser.add_argument("--addr", "--host", dest="addr", default="127.0.0.1",
                        help="监听地址（--host 与 --addr 等价）")
    parser.add_argument("--port", type=int, default=8008, help="监听端口")
    parser.add_argument("--workers", type=int, default=4,
                        help="回调并发线程数（0=同步串行模式）")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warn", "error"],
                        help="日志级别（默认 info）")
    parser.add_argument("--instance", default="smtp",
                        help="实例名（注册中心用，默认 smtp）")
    args = parser.parse_args()

    # 应用日志级别过滤（低于阈值的日志不进队列）
    logs.set_log_level(args.log_level)

    print(f"[smtp] instance={args.instance} log-level={args.log_level}")
    print(f"[smtp] 配置来源: .env / ENV > 默认值（SMTP 账号不通过 CLI 传递）")
    server = SmtpServer(args.addr, args.port, workers=args.workers)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
