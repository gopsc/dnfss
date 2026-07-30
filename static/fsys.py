# file_server.py - 极简版文件托管服务器
from flask import Flask, send_from_directory, jsonify, request, Response, send_file
from flask_cors import CORS
import os
import argparse
import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import configparser
import sys

# pymsg 日志客户端（集中式日志 → dbv2 Logs 表）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from log_client import logs  # noqa: E402
from security_middleware import apply_security_headers  # noqa: E402

# 基准目录：编译后取可执行文件所在目录，开发时取脚本所在目录
# 解决 Nuitka onefile 解压到 /tmp 后相对路径失效的问题
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

file_handler = RotatingFileHandler(os.path.join(BASE_DIR, 'app.log'), maxBytes=10485760, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s'
))
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

parser = argparse.ArgumentParser(description='File Server')
parser.add_argument('--config', type=str, default='config.ini', help='Configuration file path')
args = parser.parse_args()

app = Flask(__name__)
apply_security_headers(app)

# ==================== 配置文件加载 ====================
CONFIG_FILE = args.config
if not os.path.isabs(CONFIG_FILE):
    CONFIG_FILE = os.path.join(BASE_DIR, CONFIG_FILE)

file_config = {
    'server': {'host': '0.0.0.0', 'port': '5000'},
    'directory': {'root': './public'},
    'ssl': {'enabled': 'false', 'cert_file': 'cert.pem', 'key_file': 'key.pem'},
    # CORS 白名单（逗号分隔），生产环境默认值；localhost 仅限本地开发时显式写入 config.ini
    'cors': {'allowed_origins': 'https://qsont.me,https://yhdet.top'},
}

if os.path.exists(CONFIG_FILE):
    config_parser = configparser.ConfigParser()
    config_parser.read(CONFIG_FILE, encoding='utf-8')
    for section in ['server', 'directory', 'ssl', 'cors']:
        if section in config_parser:
            for key, value in config_parser[section].items():
                if key in file_config[section]:
                    file_config[section][key] = value

# ==================== CORS（配置加载完成后初始化） ====================
_cors_origins = [o.strip() for o in file_config['cors']['allowed_origins'].split(',') if o.strip()]
CORS(app, origins=_cors_origins)

HTML_ROOT_DIR = file_config['directory']['root']
if HTML_ROOT_DIR and not os.path.isabs(HTML_ROOT_DIR):
    HTML_ROOT_DIR = os.path.join(BASE_DIR, HTML_ROOT_DIR)
FILESYSTEM_ENABLED = bool(HTML_ROOT_DIR and HTML_ROOT_DIR.strip())

if FILESYSTEM_ENABLED:
    try:
        Path(HTML_ROOT_DIR).mkdir(parents=True, exist_ok=True)
        logger.info(f"Created directory: {HTML_ROOT_DIR}")
        logs.info("static", f"目录已创建: {HTML_ROOT_DIR}")
    except Exception as e:
        logger.error(f"Failed to create directory: {str(e)}")
        logs.error("static", f"目录创建失败: {str(e)}",
                    extra={"path": HTML_ROOT_DIR})
        FILESYSTEM_ENABLED = False

SSL_ENABLED = file_config['ssl'].get('enabled', 'false').lower() == 'true'
SSL_CERT_FILE = file_config['ssl'].get('cert_file', 'cert.pem')
SSL_KEY_FILE = file_config['ssl'].get('key_file', 'key.pem')
if not os.path.isabs(SSL_CERT_FILE):
    SSL_CERT_FILE = os.path.join(BASE_DIR, SSL_CERT_FILE)
if not os.path.isabs(SSL_KEY_FILE):
    SSL_KEY_FILE = os.path.join(BASE_DIR, SSL_KEY_FILE)

# 视图文件名：根路径与目录访问时固定返回此静态文件，由前端 JS 调用 /api/filelist 拉取数据
VIEW_FILE = '__view.html'

# ==================== 视频文件支持 ====================
VIDEO_MIME_TYPES = {
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.ogg': 'video/ogg',
    '.avi': 'video/x-msvideo', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'
}

AUDIO_MIME_TYPES = {
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.flac': 'audio/flac', '.aac': 'audio/aac', '.m4a': 'audio/mp4'
}

def is_video_file(filename):
    return os.path.splitext(filename)[1].lower() in VIDEO_MIME_TYPES

def is_audio_file(filename):
    return os.path.splitext(filename)[1].lower() in AUDIO_MIME_TYPES

def get_media_mime_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    return VIDEO_MIME_TYPES.get(ext) or AUDIO_MIME_TYPES.get(ext) or 'application/octet-stream'

def safe_path_join(base_dir, user_path):
    if not user_path or user_path.strip() == '' or user_path.strip() == '/':
        return base_dir
    if user_path.startswith('/'):
        user_path = user_path[1:]
    full_path = os.path.normpath(os.path.join(base_dir, user_path))
    base_dir_real = os.path.realpath(base_dir)
    full_path_real = os.path.realpath(full_path)
    if not full_path_real.startswith(base_dir_real):
        return None
    return full_path_real

def format_file_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"

# ==================== API 接口 ====================
@app.route('/api/filelist', methods=['GET'])
def get_file_list():
    try:
        if not FILESYSTEM_ENABLED:
            return jsonify({'error': '文件系统未启用'}), 403

        requested_path = request.args.get('path', '')
        full_path = safe_path_join(HTML_ROOT_DIR, requested_path)

        if full_path is None:
            return jsonify({'error': '无效的路径'}), 403

        if not os.path.exists(full_path) or not os.path.isdir(full_path):
            return jsonify({'error': '目录不存在'}), 404

        items = []
        for item in os.listdir(full_path):
            # 过滤系统项目：以双下划线开头的文件和文件夹（如 __pycache__、__view.html、__icons 等）
            if item.startswith('__'):
                continue

            item_path = os.path.join(full_path, item)
            item_stat = os.stat(item_path)
            is_dir = os.path.isdir(item_path)

            item_rel_path = os.path.join(requested_path, item) if requested_path else item

            file_type = 'dir' if is_dir else ('video' if is_video_file(item) else ('audio' if is_audio_file(item) else 'file'))

            items.append({
                'name': item,
                'type': file_type,
                'path': item_rel_path,
                'size': item_stat.st_size if not is_dir else 0,
                'size_formatted': format_file_size(item_stat.st_size) if not is_dir else '',
                'modified': datetime.datetime.fromtimestamp(item_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })

        items.sort(key=lambda x: (x['type'] != 'dir', x['name'].lower()))

        return jsonify({
            'success': True,
            'path': requested_path or '/',
            'parent_path': os.path.dirname(requested_path) if requested_path and os.path.dirname(requested_path) != '/' else '',
            'items': items
        })
    except Exception as e:
        logger.error(f"Get file list error: {str(e)}")
        logs.error("static", f"获取文件列表失败: {str(e)}",
                    extra={"path": requested_path})
        return jsonify({'error': str(e)}), 500

@app.route('/video/<path:file_path>', methods=['GET'])
def stream_video(file_path):
    try:
        if not FILESYSTEM_ENABLED:
            return jsonify({'error': '文件系统未启用'}), 403

        full_path = safe_path_join(HTML_ROOT_DIR, file_path)
        if full_path is None or not os.path.exists(full_path) or not os.path.isfile(full_path):
            return jsonify({'error': '文件不存在'}), 404

        if not is_video_file(full_path) and not is_audio_file(full_path):
            return jsonify({'error': '不支持的文件类型'}), 400

        file_size = os.path.getsize(full_path)
        mime_type = get_media_mime_type(full_path)

        range_header = request.headers.get('Range', None)

        if range_header:
            byte_range = range_header.replace('bytes=', '').split('-')
            start = int(byte_range[0]) if byte_range[0] else 0
            end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else file_size - 1

            if start >= file_size:
                return jsonify({'error': '请求范围超出文件大小'}), 416

            length = end - start + 1

            with open(full_path, 'rb') as f:
                f.seek(start)
                data = f.read(length)

            response = Response(data, 206, mimetype=mime_type, direct_passthrough=True)
            response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
            response.headers.add('Accept-Ranges', 'bytes')
            response.headers.add('Content-Length', str(length))
            response.headers.add('Cache-Control', 'no-cache')
            return response
        else:
            return send_file(full_path, mimetype=mime_type, as_attachment=False)
    except Exception as e:
        logger.error(f"Video streaming error: {str(e)}")
        logs.error("static", f"视频流传输失败: {str(e)}",
                    extra={"file": file_path})
        return jsonify({'error': str(e)}), 500

GATEWAY_DB = '/opt/micro_servers/gateway/sqlite3.db'


@app.route('/api/hash', methods=['POST'])
def api_hash():
    from flask import request, jsonify
    import hashlib
    data = request.get_json(force=True)
    h = hashlib.sha256(data.get('password','').encode()).hexdigest()
    return jsonify({"hash": h})


@app.route('/api/reset_password', methods=['POST'])
def api_reset_password():
    from flask import request, jsonify
    import sqlite3 as _sql
    data = request.get_json(force=True)
    email = data.get('email', '').strip()
    new_hash = data.get('password_hash', '').strip()
    if not email or not new_hash:
        return jsonify({"success": False, "error": "missing params"}), 400
    conn = _sql.connect(GATEWAY_DB)
    conn.execute("UPDATE Users SET password_hash=? WHERE email=?", (new_hash, email))
    conn.commit()
    conn.close()
    logs.info("static", "密码重置", extra={"email": email[:3] + "***"})
    return jsonify({"success": True})


@app.route('/api/check_user', methods=['POST'])
def api_check_user():
    from flask import request, jsonify
    import sqlite3 as _sql
    data = request.get_json(force=True)
    email = data.get('email', '').strip()
    if not email:
        return jsonify({"success": False, "error": "missing email"}), 400
    conn = _sql.connect(GATEWAY_DB)
    rows = conn.execute("SELECT email FROM Users WHERE email=?", (email,)).fetchall()
    conn.close()
    return jsonify({"success": True, "found": len(rows) > 0})




YHDET_VERIFY_URL = "https://yhdet.top/api/verify_auth_token"
YHDET_SECRET = "yhdet_qsont_oauth_2026_secret_key"
GATEWAY_DB_YHDET = '/opt/micro_servers/gateway/sqlite3.db'


def _yhdet_verify_token(token):
    import traceback
    import urllib.request, json
    try:
        data = json.dumps({"token": token}).encode()
        req = urllib.request.Request(YHDET_VERIFY_URL, data=data,
                headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        traceback.print_exc()
        raise


@app.route('/yhdet_callback', methods=['GET', 'POST'])
def yhdet_callback():
    from flask import request, make_response
    import hashlib, sqlite3, base64, json as _json, urllib.request as _urlreq
    token = (request.args.get('yhdet_token', '') or request.form.get('yhdet_token', '')).strip()
    if not token:
        return make_response("No token", 400)
    try:
        result = _yhdet_verify_token(token)
        if not result.get("success"):
            return make_response("Verify failed: " + str(result), 401)
        username = result.get("username", "")
        user_id = result.get("user_id", 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return make_response("Verify error: " + str(e), 500)
    email = f"yhdet_{user_id}@yhdet.oauth"
    password = f"oauth_{user_id}"
    conn = sqlite3.connect(GATEWAY_DB_YHDET)
    rows = conn.execute("SELECT email FROM Users WHERE email=?", (email,)).fetchall()
    if not rows:
        fake_hash = hashlib.sha256(password.encode()).hexdigest()
        conn.execute("INSERT INTO Users (email, password_hash) VALUES (?, ?)", (email, fake_hash))
        conn.commit()
        logs.info("static", "OAuth 新用户注册", extra={"email": email})
    conn.close()
    try:
        login_data = _json.dumps({"username": email, "password": password}).encode()
        req = _urlreq.Request("https://qsont.me/login", data=login_data,
            headers={"Content-Type": "application/json"}, method="POST")
        with _urlreq.urlopen(req, timeout=10, context=__import__('ssl')._create_unverified_context()) as resp:
            login_result = _json.loads(resp.read().decode("utf-8"))
            real_token = login_result.get("data", {}).get("token", "")
    except Exception as e:
        import traceback
        traceback.print_exc()
        real_token = ""
    html = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body><p>Login...</p><script>"
    if real_token:
        html += "localStorage.setItem('q4_token','" + real_token + "');"
        html += "sessionStorage.setItem('q4_token','" + real_token + "');"
    html += "location.href='/dnfs.html';"
    html += "</script></body></html>"
    return make_response(html, 200)

@app.route('/api/yhdet_login', methods=['POST'])
def yhdet_login():
    import hashlib, sqlite3, base64
    data = request.get_json(force=True) if request.is_json else {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    if not email or not password:
        return jsonify({"success": False, "error": "Missing params"}), 400
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = sqlite3.connect(GATEWAY_DB_YHDET)
    rows = conn.execute("SELECT email, password_hash FROM Users WHERE email=?", (email,)).fetchall()
    conn.close()
    if not rows:
        logs.warning("static", "yhdet 登录失败-用户不存在",
                      extra={"email": email[:8] + "***"})
        return jsonify({"success": False, "error": "Invalid credentials"})
    if rows[0][1] != pwd_hash:
        logs.warning("static", "yhdet 登录失败-密码错误",
                      extra={"email": email[:8] + "***"})
        return jsonify({"success": False, "error": "Invalid credentials"})
    logs.info("static", "yhdet 登录成功",
               extra={"email": email[:8] + "***"})
    token_data = f"{email}:{password}"
    token = base64.urlsafe_b64encode(token_data.encode()).decode()
    return jsonify({"success": True, "token": token})


@app.route('/api/yhdet_verify', methods=['POST'])
def yhdet_verify():
    import base64, hashlib, sqlite3
    data = request.get_json(force=True)
    token = data.get("token", "")
    if not token:
        return jsonify({"success": False}), 400
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        parts = decoded.split(":")
        if len(parts) == 3 and parts[1] == "oauth":
            return jsonify({"success": True, "email": parts[0], "type": "oauth"})
        if len(parts) == 2:
            email, password = parts
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()
            conn = sqlite3.connect(GATEWAY_DB_YHDET)
            rows = conn.execute("SELECT email FROM Users WHERE email=? AND password_hash=?", (email, pwd_hash)).fetchall()
            conn.close()
            if rows:
                return jsonify({"success": True, "email": email, "type": "password"})
        return jsonify({"success": False})
    except:
        return jsonify({"success": False})




PRIVATE_SECRET = "qsont_me_yhdet_2026_private"
YHDET_PRIVATE_API = "https://yhdet.top/api/v1/private"


from flask import make_response
import urllib.request as _urlreq, json as _json

DNFS_URL = "http://127.0.0.1:8006"

def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

@app.route('/get_notes', methods=['POST', 'OPTIONS'])
def proxy_get_notes():
    if request.method == "OPTIONS": return _cors(make_response("", 204))
    try:
        data = request.get_json(force=True)
        h = {"Content-Type": "application/json"}
        if "Authorization" in request.headers: h["Authorization"] = request.headers["Authorization"]
        elif request.cookies.get("q4_token"): h["Authorization"] = f"Bearer {request.cookies['q4_token']}"  # HttpOnly Cookie 模式：Cookie 转 Bearer
        req = _urlreq.Request(f"{DNFS_URL}/get_notes", data=_json.dumps(data).encode(), headers=h, method="POST")
        with _urlreq.urlopen(req, timeout=15) as r:
            return _cors(make_response(r.read().decode(), 200, {"Content-Type": "application/json"}))
    except Exception as e:
        logs.warning("static", "dnfs get_notes 代理失败",
                      extra={"error": str(e)})
        return _cors(make_response(_json.dumps({"error": str(e)}), 502, {"Content-Type": "application/json"}))

@app.route('/add_note', methods=['POST', 'OPTIONS'])
def proxy_add_note():
    if request.method == "OPTIONS": return _cors(make_response("", 204))
    try:
        data = request.get_json(force=True)
        h = {"Content-Type": "application/json"}
        if "Authorization" in request.headers: h["Authorization"] = request.headers["Authorization"]
        elif request.cookies.get("q4_token"): h["Authorization"] = f"Bearer {request.cookies['q4_token']}"  # HttpOnly Cookie 模式：Cookie 转 Bearer
        req = _urlreq.Request(f"{DNFS_URL}/add_note", data=_json.dumps(data).encode(), headers=h, method="POST")
        with _urlreq.urlopen(req, timeout=15) as r:
            return _cors(make_response(r.read().decode(), 200, {"Content-Type": "application/json"}))
    except Exception as e:
        logs.warning("static", "dnfs add_note 代理失败", extra={"error": str(e)})
        return _cors(make_response(_json.dumps({"error": str(e)}), 502, {"Content-Type": "application/json"}))

@app.route('/delete_note', methods=['POST', 'OPTIONS'])
def proxy_delete_note():
    if request.method == "OPTIONS": return _cors(make_response("", 204))
    try:
        data = request.get_json(force=True)
        h = {"Content-Type": "application/json"}
        if "Authorization" in request.headers: h["Authorization"] = request.headers["Authorization"]
        elif request.cookies.get("q4_token"): h["Authorization"] = f"Bearer {request.cookies['q4_token']}"  # HttpOnly Cookie 模式：Cookie 转 Bearer
        req = _urlreq.Request(f"{DNFS_URL}/delete_note", data=_json.dumps(data).encode(), headers=h, method="POST")
        with _urlreq.urlopen(req, timeout=15) as r:
            return _cors(make_response(r.read().decode(), 200, {"Content-Type": "application/json"}))
    except Exception as e:
        logs.warning("static", "dnfs delete_note 代理失败", extra={"error": str(e)})
        return _cors(make_response(_json.dumps({"error": str(e)}), 502, {"Content-Type": "application/json"}))

@app.route('/update_note', methods=['POST', 'OPTIONS'])
def proxy_update_note():
    if request.method == "OPTIONS": return _cors(make_response("", 204))
    try:
        data = request.get_json(force=True)
        h = {"Content-Type": "application/json"}
        if "Authorization" in request.headers: h["Authorization"] = request.headers["Authorization"]
        elif request.cookies.get("q4_token"): h["Authorization"] = f"Bearer {request.cookies['q4_token']}"  # HttpOnly Cookie 模式：Cookie 转 Bearer
        req = _urlreq.Request(f"{DNFS_URL}/update_note", data=_json.dumps(data).encode(), headers=h, method="POST")
        with _urlreq.urlopen(req, timeout=15) as r:
            return _cors(make_response(r.read().decode(), 200, {"Content-Type": "application/json"}))
    except Exception as e:
        logs.warning("static", "dnfs update_note 代理失败",
                      extra={"error": str(e)})
        return _cors(make_response(_json.dumps({"error": str(e)}), 502, {"Content-Type": "application/json"}))


@app.route('/whoami', methods=['POST', 'OPTIONS'])
def proxy_whoami():
    if request.method == "OPTIONS": return _cors(make_response("", 204))
    try:
        data = request.get_json(force=True, silent=True) or {}
        h = {"Content-Type": "application/json"}
        if "Authorization" in request.headers: h["Authorization"] = request.headers["Authorization"]
        elif request.cookies.get("q4_token"): h["Authorization"] = f"Bearer {request.cookies['q4_token']}"  # HttpOnly Cookie 模式：Cookie 转 Bearer
        req = _urlreq.Request(f"{DNFS_URL}/whoami", data=_json.dumps(data).encode(), headers=h, method="POST")
        with _urlreq.urlopen(req, timeout=15) as r:
            return _cors(make_response(r.read().decode(), 200, {"Content-Type": "application/json"}))
    except Exception as e:
        logs.warning("static", "dnfs whoami 代理失败", extra={"error": str(e)})
        return _cors(make_response(_json.dumps({"error": str(e)}), 502, {"Content-Type": "application/json"}))


@app.route('/api/proxy_send_code', methods=['POST'])
def proxy_send_code():
    from flask import request, jsonify
    import urllib.request as _urlreq, json as _json, __import__ as _imp
    data = request.get_json(force=True)
    req_data = _json.dumps(data).encode()
    req = _urlreq.Request(f"{YHDET_PRIVATE_API}/send_code", data=req_data,
        headers={"Content-Type": "application/json", "X-Forwarded-For": "8.137.52.11", "X-Secret": PRIVATE_SECRET}, method="POST")
    ctx = _imp('ssl').create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _imp('ssl').CERT_NONE
    with _urlreq.urlopen(req, timeout=15, context=ctx) as resp:
        return jsonify(_json.loads(resp.read().decode("utf-8")))
    req = _urlreq.Request(f"{YHDET_PRIVATE_API}/check_code", data=req_data,
        headers={"Content-Type": "application/json", "X-Forwarded-For": "8.137.52.11", "X-Secret": PRIVATE_SECRET}, method="POST")
    ctx = _imp('ssl').create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _imp('ssl').CERT_NONE
    with _urlreq.urlopen(req, timeout=15, context=ctx) as resp:
        return jsonify(_json.loads(resp.read().decode("utf-8")))






# ==================== 主路由 ====================
@app.route('/')
def serve_view():
    """根路径：优先返回 index.html（社区首页），不存在时回退到 __view.html（文件浏览）"""
    if not FILESYSTEM_ENABLED:
        return "文件系统未启用", 403
    #index_path = os.path.join(HTML_ROOT_DIR, 'index.html')
    index_path = os.path.join(HTML_ROOT_DIR, 'login.html')
    if os.path.exists(index_path):
        return send_file(index_path, mimetype='text/html')
    view_path = os.path.join(HTML_ROOT_DIR, VIEW_FILE)
    if not os.path.exists(view_path):
        return f"视图文件 {VIEW_FILE} 不存在", 500
    return send_file(view_path, mimetype='text/html')

@app.route('/<path:path>')
def serve_file(path):
    try:
        if not FILESYSTEM_ENABLED:
            return "文件系统未启用", 403

        full_path = safe_path_join(HTML_ROOT_DIR, path)
        if full_path is None:
            return "无效的路径", 403

        if not os.path.exists(full_path):
            return "路径不存在", 404

        # 文件处理
        if os.path.isfile(full_path):
            if request.args.get('download') == 'true':
                return send_file(full_path, as_attachment=True, download_name=os.path.basename(full_path))

            if is_video_file(full_path) or is_audio_file(full_path):
                return send_file(full_path, mimetype=get_media_mime_type(full_path))

            # 普通文件
            with open(full_path, 'rb') as f:
                content = f.read()
            ext = os.path.splitext(full_path)[1].lower()
            mime_map = {
                '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
                '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.txt': 'text/plain',
                '.pdf': 'application/pdf', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'
            }
            return Response(content, mimetype=mime_map.get(ext, 'application/octet-stream'))

        # 目录处理：返回 __view.html，由前端 JS 根据 URL 路径调用 /api/filelist 拉取文件列表
        view_path = os.path.join(HTML_ROOT_DIR, VIEW_FILE)
        if os.path.exists(view_path):
            return send_file(view_path, mimetype='text/html')
        return "目录无法浏览", 404

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        logs.error("static", f"静态文件服务错误: {str(e)}",
                    extra={"path": path})
        import traceback
        traceback.print_exc()
        return f"服务器内部错误: {str(e)}", 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    host = file_config['server']['host']
    port = int(file_config['server']['port'])

    print("\n" + "="*50)
    print("📁 文件托管服务器 (极简版)")
    print("="*50)
    print(f"地址: http://{host}:{port}")
    print(f"目录: {os.path.abspath(HTML_ROOT_DIR) if FILESYSTEM_ENABLED else '未启用'}")
    print("="*50)
    print("\n快捷键: / 或 Ctrl+F 搜索, ESC 清空搜索")
    print("\n")

    if SSL_ENABLED and os.path.exists(SSL_CERT_FILE) and os.path.exists(SSL_KEY_FILE):
        # Waitress 不支持 TLS，SSL 场景回退到 Flask 内置服务器
        logger.warning("SSL enabled: falling back to Flask built-in server (Waitress has no TLS support)")
        app.run(host=host, port=port, ssl_context=(SSL_CERT_FILE, SSL_KEY_FILE), debug=False)
    else:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
