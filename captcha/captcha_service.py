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
import configparser
import io
import os
import random
import secrets
import string
import threading
import time

from PIL import Image, ImageDraw, ImageFont

# pymsg / log_client 导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcServer  # noqa: E402
from log_client import logs  # noqa: E402

# ── 配置加载（两层：confs 强依赖 → 环境变量覆盖）────────────────────────
# confs 是 captcha 启动的强依赖：必须成功从 confs 拉取 captcha.ini，否则直接退出。
# 环境变量优先级最高（Docker 兼容），可覆盖 confs 中的配置值。
# confs 自身的连接地址通过引导配置确定：环境变量 CONFS_HOST/CONFS_PORT
# 优先，缺省使用 127.0.0.1:8010（与其他服务一致的约定）。

# 引导配置：confs 连接地址（不依赖 confs 本身）
_confs_host = os.environ.get('CONFS_HOST', '127.0.0.1')
_confs_port = int(os.environ.get('CONFS_PORT', '8010'))

# 白名单结构：_merge_ini 仅覆盖此处列出的 section/key，防止意外字段
_captcha_config = {
    'server': {'host': '', 'port': '', 'workers': ''},
    'captcha': {'len': '', 'ttl': '', 'font_path': ''},
}


def _merge_ini(text):
    """把 ini 文本合并进 _captcha_config（仅覆盖已知的 section/key）。"""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read_string(text)
    for section in _captcha_config:
        if ini.has_section(section):
            for key, value in ini[section].items():
                if key in _captcha_config[section]:
                    _captcha_config[section][key] = value


def _fetch_from_confs():
    """从 confs 统一配置服务拉取 captcha.ini 全文。

    使用 retry_on_refused=True：confs 未就绪时 pymsg 自动阻塞重试（每秒一次，
    每 5 秒打印一次等待提示），直到 confs 启动后自动连接成功。
    RpcError（如配置文件不存在）仍直接报错退出——这是配置错误而非启动时序问题。
    """
    from pymsg import RpcClient, RpcError  # noqa: E402
    client = RpcClient(_confs_host, _confs_port, 'captcha', timeout=5,
                       retry_on_refused=True)
    try:
        data = client.call('get', {'name': 'captcha.ini'})
    except RpcError as e:
        print(f'[captcha] 致命错误: 从 confs 获取 captcha.ini 失败: {e}',
              file=_sys.stderr)
        _sys.exit(1)
    return data.get('content')


_confs_text = _fetch_from_confs()
_merge_ini(_confs_text)
_config_source = f'confs (captcha.ini @ {_confs_host}:{_confs_port})'
print(f'[captcha] 配置来源: {_config_source}')

# 环境变量覆盖（最高优先级，保持 Docker 兼容）
if os.environ.get('LISTEN_ADDR'):
    _captcha_config['server']['host'] = os.environ['LISTEN_ADDR']
if os.environ.get('LISTEN_PORT'):
    _captcha_config['server']['port'] = os.environ['LISTEN_PORT']
if os.environ.get('WORKERS'):
    _captcha_config['server']['workers'] = os.environ['WORKERS']
if os.environ.get('CAPTCHA_FONT_PATH'):
    _captcha_config['captcha']['font_path'] = os.environ['CAPTCHA_FONT_PATH']

# 配置完整性校验
_required = {
    'server': ['host', 'port', 'workers'],
    'captcha': ['len', 'ttl'],
}
for section, keys in _required.items():
    for key in keys:
        if not _captcha_config[section][key]:
            print(f'[captcha] 致命错误: 配置项 [{section}] {key} 缺失（captcha.ini 中未配置且无环境变量覆盖）',
                  file=_sys.stderr)
            _sys.exit(1)

# 解析为最终常量
CAPTCHA_LEN = int(_captcha_config['captcha']['len'])
CAPTCHA_TTL = int(_captcha_config['captcha']['ttl'])

# 字体路径：可选配置项（缺失或用空值时不致命，回退到代码默认值；
# 即使默认值文件不存在，PIL 在 truetype() 失败时会回退到 load_ Default()）。
DEFAULT_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_PATH = _captcha_config['captcha']['font_path'] or DEFAULT_FONT_PATH

logs.info("captcha", f"配置来源: {_config_source}")

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
            font = ImageFont.truetype(FONT_PATH,
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
        logs.info("captcha", f"开始监听 tcp://{self.addr}:{self.port}")
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
        logs.info("captcha", f"验证码已生成 token={token[:8]}...")
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
            logs.warning("captcha", f"验证码校验失败-无效Token token={token[:8]}...")
            return {"valid": False, "error": "invalid or expired token"}

        expected = entry['answer']
        if len(answer) != len(expected):
            valid = False
        else:
            diff = 0
            for a, b in zip(answer, expected):
                diff |= ord(a) ^ ord(b)
            valid = diff == 0

        if not valid:
            logs.warning("captcha", f"验证码校验失败-答案错误 token={token[:8]}...")

        return {"valid": valid}

    def _handle_health(self, _req):
        return {"status": "ok", "service": "captcha"}


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    default_addr = _captcha_config['server']['host']
    default_port = int(_captcha_config['server']['port'])
    default_workers = int(_captcha_config['server']['workers'])

    parser = argparse.ArgumentParser(description="captcha — pymsg 验证码服务")
    parser.add_argument("--addr", default=default_addr, help="监听地址")
    parser.add_argument("--port", type=int, default=default_port, help="监听端口")
    parser.add_argument("--workers", type=int, default=default_workers,
                        help="回调并发线程数（0=同步串行模式）")
    args = parser.parse_args()

    server = CaptchaServer(args.addr, args.port, workers=args.workers)
    print(f'[captcha] 配置来源: {_config_source}')
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
