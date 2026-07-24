"""
CAPTCHA Image Service (stdlib only, no Flask dependency)
"""
import os
import io
import json
import time
import random
import string
import secrets
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageDraw, ImageFont

CAPTCHA_LEN = 5
CAPTCHA_TTL = 300
ADDR = '127.0.0.1'
PORT = 8009

CHARS = string.ascii_letters + string.digits
CHARS = CHARS.translate(str.maketrans('', '', 'lI1O0o'))

captcha_store = {}
STORE_LOCK = threading.Lock()


def generate_text():
    return ''.join(random.choices(CHARS, k=CAPTCHA_LEN))


def generate_image(text):
    width, height = 160, 50
    bg = random.choice([(255,255,255), (240,240,240), (230,240,250)])
    img = Image.new('RGB', (width, height), bg)
    draw = ImageDraw.Draw(img)

    for i, ch in enumerate(text):
        color = random.choice([(30,30,30),(50,80,150),(150,50,50),(50,130,50)])
        y = random.randint(2, 12)
        x = 10 + i * 28 + random.randint(-3, 3)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                      random.choice([28,30,32,34]))
        except (IOError, OSError):
            font = ImageFont.load_default()
        draw.text((x, y), ch, fill=color, font=font)

    for _ in range(4):
        draw.line([(random.randint(0,width), random.randint(0,height)),
                   (random.randint(0,width), random.randint(0,height))],
                  fill=random.choice([(30,30,30),(50,80,150)]), width=1)
    for _ in range(50):
        draw.point((random.randint(0,width-1), random.randint(0,height-1)),
                   fill=random.choice([(30,30,30),(50,80,150)]))

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


def json_response(handler, data, status=200):
    body = json.dumps(data).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class CaptchaHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/captcha/image':
            text = generate_text()
            token = secrets.token_hex(16)
            with STORE_LOCK:
                captcha_store[token] = {'answer': text.lower(), 'created_at': time.time()}
                now = time.time()
                expired = [k for k, v in captcha_store.items() if now - v['created_at'] > CAPTCHA_TTL]
                for k in expired:
                    del captcha_store[k]

            img_buf = generate_image(text)
            data = img_buf.read()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Captcha-Token', token)
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == '/health':
            json_response(self, {'status': 'ok', 'service': 'captcha'})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/captcha/verify':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                json_response(self, {'valid': False, 'error': 'invalid json'}, 400)
                return

            token = data.get('token', '').strip()
            answer = data.get('answer', '').strip().lower()
            if not token or not answer:
                json_response(self, {'valid': False, 'error': 'token and answer required'}, 400)
                return

            with STORE_LOCK:
                entry = captcha_store.pop(token, None)

            if not entry:
                json_response(self, {'valid': False, 'error': 'invalid or expired token'}, 400)
                return

            expected = entry['answer']
            if len(answer) != len(expected):
                valid = False
            else:
                diff = 0
                for a, b in zip(answer, expected):
                    diff |= ord(a) ^ ord(b)
                valid = diff == 0

            json_response(self, {'valid': valid})
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == '__main__':
    server = HTTPServer((ADDR, PORT), CaptchaHandler)
    print(f'CAPTCHA service listening at http://{ADDR}:{PORT}')
    server.serve_forever()
