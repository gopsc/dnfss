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
CORS(app)

# ==================== 配置文件加载 ====================
CONFIG_FILE = args.config
if not os.path.isabs(CONFIG_FILE):
    CONFIG_FILE = os.path.join(BASE_DIR, CONFIG_FILE)

file_config = {
    'server': {'host': '0.0.0.0', 'port': '5000'},
    'directory': {'root': './public'},
    'ssl': {'enabled': 'false', 'cert_file': 'cert.pem', 'key_file': 'key.pem'},
}

if os.path.exists(CONFIG_FILE):
    config_parser = configparser.ConfigParser()
    config_parser.read(CONFIG_FILE, encoding='utf-8')
    for section in ['server', 'directory', 'ssl']:
        if section in config_parser:
            for key, value in config_parser[section].items():
                if key in file_config[section]:
                    file_config[section][key] = value

HTML_ROOT_DIR = file_config['directory']['root']
if HTML_ROOT_DIR and not os.path.isabs(HTML_ROOT_DIR):
    HTML_ROOT_DIR = os.path.join(BASE_DIR, HTML_ROOT_DIR)
FILESYSTEM_ENABLED = bool(HTML_ROOT_DIR and HTML_ROOT_DIR.strip())

if FILESYSTEM_ENABLED:
    try:
        Path(HTML_ROOT_DIR).mkdir(parents=True, exist_ok=True)
        logger.info(f"Created directory: {HTML_ROOT_DIR}")
    except Exception as e:
        logger.error(f"Failed to create directory: {str(e)}")
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
        return jsonify({'error': str(e)}), 500

# ==================== 主路由 ====================
@app.route('/')
def serve_view():
    """根路径：优先返回 index.html（社区首页），不存在时回退到 __view.html（文件浏览）"""
    if not FILESYSTEM_ENABLED:
        return "文件系统未启用", 403
    index_path = os.path.join(HTML_ROOT_DIR, 'index.html')
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
        import traceback
        traceback.print_exc()
        return f"服务器内部错误: {str(e)}", 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'filesystem': {'enabled': FILESYSTEM_ENABLED, 'root': HTML_ROOT_DIR if FILESYSTEM_ENABLED else None}
    })

# ==================== 主程序 ====================
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
        app.run(host=host, port=port, ssl_context=(SSL_CERT_FILE, SSL_KEY_FILE), debug=False)
    else:
        app.run(host=host, port=port, debug=False)

