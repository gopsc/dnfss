"""
captcha — 基于 pymsg 协议（MsgServer）的图形验证码服务。

替代原 http.server HTTP 接口，通过 TCP 长度前缀帧收发 JSON。
协议：每条消息为完整 JSON，``_route`` 字段对应路由名。

请求示例::

    {"_route": "image"}                          → 生成验证码图片
    {"_route": "verify", "token": "...", "answer": "AB3XY"}  → 校验
    {"_route": "health"}                         → 健康检查

响应格式（与 dbv2 一致）::

    {"success": true,  "data": {...}}
    {"success": false, "error": "..."}

说明：PNG 图片为二进制，经 base64 编码后放入 JSON 返回；
调用方（gateway）解码后以 image/png 代理给浏览器。
"""

import base64
import io
import os
import random
import secrets
import string
import threading
import time

from PIL import Image, ImageDraw, ImageFont

# pymsg 导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcServer  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────

CAPTCHA_LEN = 4
CAPTCHA_TTL = 300        # Token 有效期（秒）

CHARS = string.ascii_letters + string.digits
CHARS = CHARS.translate(str.maketrans('', '', 'lI1O0o'))   # 剔除易混淆字符

captcha_store = {}       # token -> {'answer': str, 'created_at': float}
STORE_LOCK = threading.Lock()


# ── 验证码生成 ──────────────────────────────────────────────────────────

def generate_text():
    return ''.join(random.choices(CHARS, k=CAPTCHA_LEN))


def generate_image(text):
    width, height = 160, 50
    bg = (random.randint(0,256), random.randint(0,256), random.randint(0,256))
    img = Image.new('RGB', (width, height), bg)
    draw = ImageDraw.Draw(img)

    for i, ch in enumerate(text):
        color = (random.randint(0,256), random.randint(0,256), random.randint(0,256))
        y = random.randint(2, 12)
        x = 15 + i * 32 + random.randint(-3, 3)   # 4 位字符在 160px 宽度内均匀分布
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                      random.choice(range(20,40,2)))
        except (IOError, OSError):
            font = ImageFont.load_default()

        char = Image.new("RGBA", (64, 64), (0,0,0,0))
        draw_char = ImageDraw.Draw(char)
        draw_char.text((20,20), ch, fill=color, font=font)
        rotated_char = char.rotate(random.randint(-30, 30), expand=True)
        img.paste(rotated_char, (x-32, y-32), mask=rotated_char)
        # draw.text((x, y), ch, fill=color, font=font)

    for _ in range(4):
        draw.line([(random.randint(0, width), random.randint(0, height)),
                   (random.randint(0, width), random.randint(0, height))],
                  fill=(random.randint(0,256), random.randint(0,256), random.randint(0,256)))
    for _ in range(50):
        draw.point((random.randint(0, width - 1), random.randint(0, height - 1)),
                   fill=(random.randint(0,256), random.randint(0,256), random.randint(0,256)))

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════
# CaptchaServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class CaptchaServer:
    """基于 pymsg 的验证码服务，通过 TCP 长连接收发 JSON。"""

    def __init__(self, addr="127.0.0.1", port=8009, workers=4):
        self.addr = addr
        self.port = port
        self._server = RpcServer(addr, port, workers=workers)
        self._server.add_route("image", self._handle_image)
        self._server.add_route("verify", self._handle_verify)
        self._server.add_route("health", self._handle_health)

    # ── 启动 / 停止 ──────────────────────────────────────────────────
    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        print(f"[captcha] 监听 tcp://{self.addr}:{self.port}")
        self._server.start()

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ═══════════════════════════════════════════════════════════════════
    # 路由处理
    # ═══════════════════════════════════════════════════════════════════

    def _handle_image(self, _req):
        """生成验证码图片，返回 token 与 base64 编码的 PNG。"""
        text = generate_text()
        token = secrets.token_hex(16)

        with STORE_LOCK:
            captcha_store[token] = {'answer': text.lower(), 'created_at': time.time()}
            # 惰性清理过期项
            now = time.time()
            expired = [k for k, v in captcha_store.items() if now - v['created_at'] > CAPTCHA_TTL]
            for k in expired:
                del captcha_store[k]

        png_bytes = generate_image(text)
        return {
            "token": token,
            "image": base64.b64encode(png_bytes).decode('ascii'),
        }

    def _handle_verify(self, req):
        """校验答案，Token 一次性消费；恒定时间比较防计时攻击。"""
        token = str(req.get("token", "")).strip()
        answer = str(req.get("answer", "")).strip().lower()
        if not token or not answer:
            return {"valid": False, "error": "token and answer required"}

        with STORE_LOCK:
            entry = captcha_store.pop(token, None)

        if not entry:
            return {"valid": False, "error": "invalid or expired token"}

        expected = entry['answer']
        if len(answer) != len(expected):
            valid = False
        else:
            diff = 0
            for a, b in zip(answer, expected):
                diff |= ord(a) ^ ord(b)
            valid = diff == 0

        return {"valid": valid}

    def _handle_health(self, _req):
        return {"status": "ok", "service": "captcha"}


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="captcha — pymsg 验证码服务")
    parser.add_argument("--addr", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8009, help="监听端口")
    parser.add_argument("--workers", type=int, default=4,
                        help="回调并发线程数（0=同步串行模式）")
    args = parser.parse_args()

    server = CaptchaServer(args.addr, args.port, workers=args.workers)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
