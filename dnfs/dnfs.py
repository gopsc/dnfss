#!/usr/bin/env python3

'''

Dendrachy Network File-System (dnfs)

树状网络文件系统



可能加入数据库加密功能。

'''

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
#from flask_sqlalchemy import SQLAlchemy    # 使用pymsg模块进行通信
from waitress import serve
import os
import sys
import json
import base64
import configparser
import urllib.request
import urllib.error
from typing import Dict, Any
from security_middleware import apply_security_headers
from log_client import logs

# pymsg 导入
# 我们有在依赖列表中加入pymsg
# 这个可以省略也许
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcClient, RpcError  # noqa: E402

# ==================== 配置加载（三层 fail-safe） ====================
# 加载顺序：内置默认值 → confs 统一配置服务（dnfs.ini） → 环境变量覆盖
# 环境变量优先级最高（Docker 环境使用服务名 db/ai_review 而非 127.0.0.1）

_dnfs_config = {
    'server': {'host': '127.0.0.1', 'port': '8006'},
    'database': {'host': '127.0.0.1', 'port': '8005', 'timeout': '10'},
    'ai_review': {'url': 'http://127.0.0.1:8007/review', 'timeout': '10'},
    'confs': {'host': '127.0.0.1', 'port': '8010'},
}


def _merge_ini(text):
    """把 ini 文本合并进 _dnfs_config（仅覆盖已知的 section/key，白名单合并）。"""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read_string(text)
    for section in _dnfs_config:
        if ini.has_section(section):
            for key, value in ini[section].items():
                if key in _dnfs_config[section]:
                    _dnfs_config[section][key] = value


def _fetch_from_confs():
    """从 confs 统一配置服务拉取 dnfs.ini 全文，失败返回 None（fail-safe）。"""
    try:
        host = os.environ.get('CONFS_HOST', _dnfs_config['confs']['host'])
        port = int(os.environ.get('CONFS_PORT', _dnfs_config['confs']['port']))
        data = RpcClient(host, port, 'dnfs', timeout=3).call(
            'get', {'name': 'dnfs.ini'})
        return data.get('content')
    except Exception:
        return None


_confs_text = _fetch_from_confs()
if _confs_text is not None:
    _merge_ini(_confs_text)
    logs.info("dnfs", "配置来源: confs 统一配置服务 (dnfs.ini)")
else:
    logs.warning("dnfs", "confs 不可用，使用内置默认值 + 环境变量")

# 环境变量覆盖（优先级最高，Docker 环境需要）
if os.environ.get('LISTEN_ADDR'):
    _dnfs_config['server']['host'] = os.environ['LISTEN_ADDR']
if os.environ.get('LISTEN_PORT'):
    _dnfs_config['server']['port'] = os.environ['LISTEN_PORT']
if os.environ.get('DB_HOST'):
    _dnfs_config['database']['host'] = os.environ['DB_HOST']
if os.environ.get('DB_PORT'):
    _dnfs_config['database']['port'] = os.environ['DB_PORT']
if os.environ.get('REVIEW_URL'):
    _dnfs_config['ai_review']['url'] = os.environ['REVIEW_URL']
if os.environ.get('CONFS_HOST'):
    _dnfs_config['confs']['host'] = os.environ['CONFS_HOST']
if os.environ.get('CONFS_PORT'):
    _dnfs_config['confs']['port'] = os.environ['CONFS_PORT']

# 提取配置常量
_DBV2_ADDR = _dnfs_config['database']['host']
_DBV2_PORT = int(_dnfs_config['database']['port'])
_DB_TIMEOUT = int(_dnfs_config['database']['timeout'])
_REVIEW_SERVER_URL = _dnfs_config['ai_review']['url']
_REVIEW_TIMEOUT = int(_dnfs_config['ai_review']['timeout'])
_ADDR = _dnfs_config['server']['host']
_PORT = int(_dnfs_config['server']['port'])

# 管理员判定已上移至 gateway：登录时命中 [Admin] users 白名单即在 Token payload
# 写入 is_admin=true，本服务通过 info.get('is_admin') 读取，不再本地维护名单。
# 旧 Token 无此字段，按非管理员处理（安全方向）。

_rpc = RpcClient(_DBV2_ADDR, _DBV2_PORT, "dnfs")


def _call_dbv2(payload: dict, timeout: int = None) -> dict:
    """通过 pymsg RPC 调用 dbv2 数据库服务（自动注入 ``_route: "execute"``）。

    返回完整响应 dict（``{"success": ..., "data"/"error": ...}``），
    与历史行为一致，调用方无需改动。服务端业务错误返回 success=False；
    网络层故障抛 ConnectionError。
    """
    if timeout is None:
        timeout = _DB_TIMEOUT
    try:
        data = _rpc.call("execute", payload, timeout=timeout)
        return {"success": True, "data": data}
    except RpcError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        raise ConnectionError(
            f"dbv2 调用失败 ({_DBV2_ADDR}:{_DBV2_PORT}): {e}"
        ) from None

# AI审核服务地址
def review_content(content: str, timeout: int = None) -> Dict[str, Any]:
    """
    调用 AI 内容审核服务的 /review 接口，对给定文本进行审核。

    Args:
        content: 待审核的文本内容，不可为空。
        timeout: 请求超时时间（秒），默认使用配置值。

    Returns:
        字典，包含接口返回的字段，例如：
        - 成功时：{"invalid": "true"} 或 {"invalid": "false"}
        - API 错误时：{"invalid": "false", "error": "api_error", "detail": "..."}
        - 解析失败时：{"invalid": "false", "error": "parse_failed", "raw": "..."}
        - 请求参数错误时：{"error": "content is required"} 等
        - 网络或异常时：{"error": "request_failed", "detail": "..."}
    """
    if timeout is None:
        timeout = _REVIEW_TIMEOUT
    url = _REVIEW_SERVER_URL
    payload = {"content": content}
    json_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=json_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = resp.read().decode("utf-8")
            data = json.loads(resp_data)
            # 兼容布尔/字符串两种返回类型：仅明确判定安全才放行，其余一律拦截（fail-closed）
            return data.get('invalid') in (False, 'false')

    except urllib.error.HTTPError as e:
        # 服务端返回 4xx/5xx 错误时，尝试解析错误正文
        error_body = e.read().decode("utf-8")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError as e:
            raise e
    except urllib.error.URLError as e:
        raise e
    except Exception as e:
        raise e

class DnfsExp(Exception):
    '''dnfs异常类'''
    def __init__(self, msg: str, code: int):
        super().__init__(self, msg)
        self.status = code
    def get_status(self) -> int:
        return self.status

# 这个也许不用这么复杂 大不了每个异常点我们抛出DnfsExp类
#class InvalidRequest(DnfsExp):
#    '''非法参数异常'''
#    def __init__(self, msg: str):
#        super().__init__(msg)

def _handle_exp(func):
    '''处理视图函数中抛出的异常，并在日志模块中进行记录'''
    def wrapper():
        try: return func()
        except DnfsExp as e:
            logs.warning('dnfs', str(e), extra={})
            return jsonify({'error': str(e)}), e.get_status()
        except Exception as e:
            logs.warning('dnfs', str(e), extra={})
            return jsonify({'error': str(e)}), 500
    return wrapper

app = Flask(__name__)
apply_security_headers(app)
CORS(app)


def decode_token(token:  str):
    idx = token.find('_')
    if idx != -1:
        raw = token[:idx]
        # FIXME: 验证数字签名
        decoded = base64.b64decode(raw)
        jj = decoded.decode('utf-8')
        return json.loads(jj)
       

@app.route('/add_note', methods=['POST'])
@_handle_exp
def add_note():
    # 获取请求数据
    data = request.get_json()
    pid: int = data.get('pid')
    title: str = data.get('title')
    content: str = data.get('content')
    if pid is None or not title or not content:
        raise DnfsExp("非法参数", 400)

    info = None
    auth: str = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token: str = auth[7:]
        info  = decode_token(token)
    if info is None:
        raise DnfsExp("未授权访问，从"+request.remote_addr, 401)


    # 父节点是否存在
    if pid != 0:

        select_payload = {
            'action': 'select',
            'table': 'Notes',
            'where': {
                'id': pid
            }
        }

        notes = _call_dbv2(select_payload)

        if len( notes['data']) == 0:
            raise DnfsExp('父节点不存在', 400)


    # 内容审查
    review = review_content(title + '\n' + content)
    if True != review: raise DnfsExp('内容审核不通过，用户'+info.get('user')+'，从'+request.remote_addr, 400)

    # 执行插入
    insert_payload = {
        'action': 'insert',
        'table': 'Notes',
        'values': {
            "pid": pid,
            'owner': info['user'],
            "title": title,
            'content': content
        }
    }

    result = _call_dbv2(insert_payload)
    success = result['success']
    if success is True:
        logs.info('dnfs', f'add_note 成功 title={title} pid={pid}', extra={'user': info['user']})
        return jsonify({'success': success}), 200
    else:
        raise DnfsExp('插入节点失败，用户'+info.get('user')+'，从'+request.remote_addr, 400)


# {“pid”: 0} 
@_handle_exp
@app.route('/get_notes', methods=["POST"])
def get_notes():

    data = request.get_json()

    pid = data.get('pid')

    if not pid: pid = 0

    select_payload = {
        'action': 'select',
        'table': 'Notes',
        'where': {
            'pid': pid
        }
    }

    notes = _call_dbv2(select_payload)

    result = notes['data']

    # 解析当前用户（可选）：为每个节点附带操作权标记 can_operate，
    # 前端据此显隐编辑/删除按钮。口径与 delete_note / update_note 一致：
    # 管理员（is_admin）或节点 owner 有权。
    # get_notes 保持公开：未携带 Token 或 Token 解析失败时，所有节点一律无权。
    info = None
    auth: str = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        try:
            info = decode_token(auth[7:])
        except Exception:
            info = None

    for note in result:
        note['can_operate'] = bool(
            info and (info.get('is_admin') or note.get('owner') == info.get('user'))
        )

    logs.info('dnfs', f'get_notes 查询 pid={pid} 返回{len(result)}条', extra={'ip': request.remote_addr})
    return jsonify(result), 200


# eg: {"id": 1}
@app.route('/delete_note', methods=['POST'])

def delete_note():

    data = request.get_json()

    note_id: int = data.get('id')

    if not note_id:

        logs.warning('dnfs', 'delete_note 参数缺失 id', extra={'ip': request.remote_addr})
        return jsonify({"error": "Note ID is required"}), 400

    info = None
    auth: str = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token: str = auth[7:]
        info  = decode_token(token)

    if info is None:
        logs.warning('dnfs', 'delete_note 未授权访问', extra={'ip': request.remote_addr})
        return jsonify({"error": "Warning: no Authorization"}), 401

    #with app.app_context():

    #    notes = Note.query.filter(Note.pid == int(note_id))

    #    note = Note.query.get(note_id)

    #    if note:

    #        db.session.delete(note)

    #        db.session.commit()

    #        return jsonify({'message': "Note deleted successfully"}), 200

    #    else:

    #        return jsonify({"error": "Note not found"}), 404


    # 检查是否有子节点
    select_payload = {
        'action': 'select',
        'table': 'Notes',
        'where': {
            'pid': note_id
        }
    }

    notes = _call_dbv2(select_payload)

    # 有子节点的情况不可删除
    if len(notes['data']) > 0:

        logs.warning('dnfs', f'delete_note 存在子节点 id={note_id}', extra={'user': info['user']})
        return jsonify({'error': 'this node has a child '}), 400

    # 检查所有权
    select_payload = {
        'action': 'select',
        'table': 'Notes',
        'where': {
            'id': note_id
        }
    }

    data = _call_dbv2(select_payload)
    notes = data['data']
    if not info.get('is_admin'):
        if not len(notes) == 1 or not notes[0]['owner'] == info['user']:
            logs.warning('dnfs', f'delete_note 所有权拒绝 id={note_id}', extra={'user': info['user']})
            return jsonify({"error": 'It\'s not your note'}), 403


    delete_payload = {
        'action': 'delete',
        'table': 'Notes',
        'where': {'id': note_id}
    }

    result = _call_dbv2(delete_payload)

    result = result['success']

    if result is True:

        logs.info('dnfs', f'delete_note 成功 id={note_id}', extra={'user': info['user']})
        return jsonify({'success': True}), 200

    else:

        logs.error('dnfs', f'delete_note 失败 id={note_id}', extra={'user': info['user']})
        return jsonify({'success': False}), 400


# eg: {
#    "id": 1,
#    "pid": 0,
#    "title": "test",
#    "content": "hello"
#}
@app.route('/update_note', methods=['POST'])

def update_note():

    data = request.get_json()

    id: int = data.get('id')

    pid: int  = data.get('pid')

    title: str = data.get('title')

    content: str = data.get('content')

    if id is None or pid is None or not title or not content:

        logs.warning('dnfs', 'update_note 参数缺失', extra={'ip': request.remote_addr})
        return jsonify({"error": "Invalid param"}), 400

    # 获取用户名
    info = None
    auth: str = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token: str = auth[7:]
        info  = decode_token(token)

    if info is None:
        logs.warning('dnfs', 'update_note 未授权访问', extra={'ip': request.remote_addr})
        return jsonify({"error": "Warning: no Authorization"}), 401


    # 检查所有权
    select_payload = {
        'action': 'select',
        'table': 'Notes',
        'where': {
            'id': id
        }
    }

    data = _call_dbv2(select_payload)
    notes = data['data']
    if not info.get('is_admin'):
        if not len(notes) == 1 or not notes[0]['owner'] == info['user']:
            logs.warning('dnfs', f'update_note 所有权拒绝 id={id}', extra={'user': info['user']})
            return jsonify({"error": 'It\'s not your note'}), 403

    # 内容审查
    review = review_content(title + '\n' + content)
    if True != review:
        #return jsonify({'error': 'review failed', 'data': review}), 400
        logs.warning('dnfs', f'update_note 内容审核未通过 id={id} title={title}', extra={'user': info['user']})
        return jsonify({'error': 'review failed'}), 400

    #with app.app_context():

    #    note = Note.query.get(id)

    #    if note:

    #        note.pid = pid

    #        note.title = title

    #        note.content = content

    #        db.session.commit()

    update_payload = {
        "action": "update",
        'table': 'Notes',
        "values": {
            'pid': pid,
            'title': title,
            'content': content
        },
        'where': {'id': id}
    }

    result = _call_dbv2(update_payload)

    result = result['success']

    if result is True:

        logs.info('dnfs', f'update_note 成功 id={id} title={title}', extra={'user': info['user']})
        return jsonify({'success': True}), 200

    else:

        logs.error('dnfs', f'update_note 失败 id={id}', extra={'user': info['user']})
        return jsonify({'success': False}), 400

## by shufeng
## 读取单个节点
'''@app.route("/get_note", methods=["POST"])

def get_note():
    data = request.get_json()

    id: int = data.get("id")
    auth: str = request.headers.get("Authorization", "")
    info = None

    if auth.startswith("Bearer "):
        token: str = auth[7:]
        info = decode_token(token)

    if id is None:
        logs.error("dnfs", "get_note: 参数不足，缺少id")
        return jsonify({"error": "Invalid param"}), 401

    select_payload = {
        "method": "select",
        "tables": "Notes",
        "where": {
            "id": id
        }
    }

    data = _call_dbv2(select_payload)
    notes = data["data"]

    if not len(notes) == 1 or not notes[0]["owner"] == info["user"]:
        logs.error("dnfs", "没有读取权")
        return jsonify({"error": "你没有读取这个的权力"}), 403

    logs.info("dnfs", f"get_note 查询 id={id}")
    return jsonify()
'''



#@app.route('/')
#def view_page():
#    """返回应用主页"""
#    return render_template('index.html')

if __name__ == "__main__":
    logs.info('dnfs', f'服务启动，监听 http://{_ADDR}:{_PORT}（db={_DBV2_ADDR}:{_DBV2_PORT}, ai_review={_REVIEW_SERVER_URL}）')
    print(f'Listen at http://{_ADDR}:{_PORT}')
    serve(app, host=_ADDR, port=_PORT)
