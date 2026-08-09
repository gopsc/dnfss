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
import configparser
import urllib.request
import urllib.error
import functools
from typing import Dict, Any
from security_middleware import apply_security_headers
from log_client import logs

# pymsg 导入
# 我们有在依赖列表中加入pymsg
# 这个可以省略也许
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcClient, RpcError  # noqa: E402

# ==================== 配置加载（两层：confs 强依赖 → 环境变量覆盖） ====================
# confs 是 dnfs 启动的强依赖：必须成功从 confs 拉取 dnfs.ini，否则直接退出。
# 环境变量优先级最高（Docker 环境使用服务名 db/ai_review 而非 127.0.0.1）。
# confs 自身的连接地址通过引导配置确定：环境变量 CONFS_HOST/CONFS_PORT 优先，
# 缺省使用 127.0.0.1:8010（与其他服务一致的约定）。

# 引导配置：confs 连接地址（不依赖 confs 本身）
_confs_host = os.environ.get('CONFS_HOST', '127.0.0.1')
_confs_port = int(os.environ.get('CONFS_PORT', '8010'))

# 白名单结构：_merge_ini 仅覆盖此处列出的 section/key，防止意外字段
_dnfs_config = {
    'server': {'host': '', 'port': ''},
    'database': {'host': '', 'port': '', 'timeout': ''},
    'ai_review': {'url': '', 'timeout': ''},
    'gateway': {'url': '', 'timeout': ''},
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
    """从 confs 统一配置服务拉取 dnfs.ini 全文。

    使用 retry_on_refused=True：confs 未就绪时 pymsg 自动阻塞重试（每秒一次，
    每 5 秒打印一次等待提示），直到 confs 启动后自动连接成功。
    RpcError（如配置文件不存在）仍直接报错退出——这是配置错误而非启动时序问题。
    """
    try:
        data = RpcClient(_confs_host, _confs_port, 'dnfs', timeout=5,
                         retry_on_refused=True).call('get', {'name': 'dnfs.ini'})
    except RpcError as e:
        print(f'[dnfs] 致命错误: 从 confs 获取 dnfs.ini 失败: {e}',
              file=sys.stderr)
        sys.exit(1)
    return data.get('content')


_confs_text = _fetch_from_confs()
_merge_ini(_confs_text)

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
if os.environ.get('GATEWAY_URL'):
    _dnfs_config['gateway']['url'] = os.environ['GATEWAY_URL']

# 配置完整性校验
_required = {
    'server': ['host', 'port'],
    'database': ['host', 'port', 'timeout'],
    'ai_review': ['url', 'timeout'],
    'gateway': ['url', 'timeout'],
}
for section, keys in _required.items():
    for key in keys:
        if not _dnfs_config[section][key]:
            print(f'[dnfs] 致命错误: 配置项 [{section}] {key} 缺失（dnfs.ini 中未配置且无环境变量覆盖）',
                  file=sys.stderr)
            sys.exit(1)

# 提取配置常量
_DB_TIMEOUT = int(_dnfs_config['database']['timeout'])
_DBV2_ADDR = _dnfs_config['database']['host']
_DBV2_PORT = int(_dnfs_config['database']['port'])
_REVIEW_SERVER_URL = _dnfs_config['ai_review']['url']
_REVIEW_TIMEOUT = int(_dnfs_config['ai_review']['timeout'])
_GATEWAY_URL = _dnfs_config['gateway']['url']
_GATEWAY_TIMEOUT = int(_dnfs_config['gateway']['timeout'])
_ADDR = _dnfs_config['server']['host']
_PORT = int(_dnfs_config['server']['port'])

_config_source = f'confs (dnfs.ini @ {_confs_host}:{_confs_port})'
print(f'[dnfs] 配置来源: {_config_source}')
print(f'[dnfs] Token 验签: gateway {_GATEWAY_URL}')

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

# ==================== 特殊节点（管理员专属） ====================
# 信封格式：content 为 JSON {"_sp": "link|js|image|file", ...}，前端识别后差异化渲染；
# 普通 Markdown 内容不受影响。仅管理员可发布（Token payload is_admin）。
_SP_TYPES = ('link', 'js', 'image', 'file')
_SP_MAX_URL = 2048                    # link 节点 url 长度上限
_SP_MAX_CODE = 512 * 1024             # js 节点代码长度上限（字符）
_SP_MAX_DATA = 3500 * 1024            # image/file 节点 base64 长度上限（字符，约 2.6MB 原始数据）
_SP_REVIEW_CODE_PREFIX = 4000         # js 节点送审的代码前缀长度


def _parse_sp(content):
    """识别特殊节点信封：合法信封返回 dict；普通内容返回 None；
    以 {"_sp" 开头但 JSON 非法视为格式错误（拒绝发布，防半截信封流入）。"""
    if not isinstance(content, str):
        return None
    s = content.lstrip()
    if not s.startswith('{"_sp"'):
        return None
    try:
        obj = json.loads(s)
    except Exception:
        raise DnfsExp('特殊节点格式非法（JSON 解析失败）', 400)
    if isinstance(obj, dict) and obj.get('_sp') in _SP_TYPES:
        return obj
    return None


def _validate_sp(sp):
    """校验特殊节点信封字段与大小上限，非法抛 DnfsExp。"""
    t = sp['_sp']
    if t == 'link':
        url = sp.get('url', '')
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            raise DnfsExp('链接节点必须是 http/https URL', 400)
        if len(url) > _SP_MAX_URL:
            raise DnfsExp('链接过长', 400)
    elif t == 'js':
        code = sp.get('code', '')
        if not isinstance(code, str) or not code.strip():
            raise DnfsExp('前端代码不能为空', 400)
        if len(code) > _SP_MAX_CODE:
            raise DnfsExp('前端代码过大（上限 512KB）', 400)
    elif t == 'image':
        data = sp.get('data', '')
        if not isinstance(data, str) or not data.startswith('data:image/'):
            raise DnfsExp('图片节点 data 必须是 data:image/ 开头的 DataURL', 400)
        if len(data) > _SP_MAX_DATA:
            raise DnfsExp('图片过大（上限约 2.6MB）', 400)
    elif t == 'file':
        name = sp.get('name', '')
        data = sp.get('data', '')
        if not isinstance(name, str) or not name.strip():
            raise DnfsExp('文件节点缺少文件名', 400)
        if len(name) > 255:
            raise DnfsExp('文件名过长', 400)
        if not isinstance(data, str) or not data:
            raise DnfsExp('文件节点缺少数据', 400)
        if len(data) > _SP_MAX_DATA:
            raise DnfsExp('文件过大（上限约 2.6MB）', 400)


def _sp_review_text(title, sp):
    """特殊节点的 AI 审核文本：剔除 base64 负载（体量大且无审核意义），仅保留文本字段。"""
    parts = [title or '']
    note = sp.get('note')
    if isinstance(note, str) and note.strip():
        parts.append(note)
    t = sp['_sp']
    if t == 'link':
        parts.append(str(sp.get('url', '')))
    elif t == 'js':
        parts.append(str(sp.get('code', ''))[:_SP_REVIEW_CODE_PREFIX])
    else:  # image / file
        parts.append(str(sp.get('name', '')))
    return '\n'.join(parts)

# 这个也许不用这么复杂 大不了每个异常点我们抛出DnfsExp类
#class InvalidRequest(DnfsExp):
#    '''非法参数异常'''
#    def __init__(self, msg: str):
#        super().__init__(msg)

def _handle_exp(func):
    '''处理视图函数中抛出的异常，并在日志模块中进行记录'''
    @functools.wraps(func)
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


# [SECURITY-AUDIT] P0-1 / P1-5 —— 已修复（审计结论见 docs/security/dnfs_SECURITY_AUDIT_20260801.md）
# 原问题：
#   · P0-1 Token 无 RSA 签名验证，攻击者可伪造任意身份（含管理员）。
#   · P1-5 Token 无过期校验，被盗 Token 可长期复用。
# 修复方式（本项目统一安全决策）：本服务不持有密钥，Token 校验全部上移至 gateway。
#   decode_token 转发 gateway /whoami，由 gateway 完成 RSA 验签 + 基于
#   created_at+keep_time 的过期校验；gateway 不可达 → fail-closed 抛 503，
#   验签失败/未登录 → 返回 None（调用方转 401）。is_admin 由 gateway 基于
#   [Admin] 白名单在 Token payload 写入，本服务只读，不再本地判定。
def decode_token(token: str):
    """通过 gateway /whoami 接口验证 Token 并返回用户信息。

    gateway /whoami 内部完成 RSA 签名验证和过期检查，返回：
      - 成功: {"login": true, "user": "...", "is_admin": true/false, ...}
      - 失败: {"login": false}
    网络异常（gateway 不可达）按 fail-closed 处理，抛 DnfsExp 拒绝请求。
    """
    if not token:
        return None

    req = urllib.request.Request(
        _GATEWAY_URL,
        data=b'{}',
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_GATEWAY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        # gateway 不可达或返回异常：fail-closed，拒绝请求
        logs.warning('dnfs', f'Token 验签失败（gateway 异常）: {e}', extra={})
        raise DnfsExp('认证服务暂时不可用', 503)
    except Exception as e:
        logs.warning('dnfs', f'Token 验签未知异常: {e}', extra={})
        raise DnfsExp('认证失败', 401)

    if not data.get('login'):
        return None

    # gateway 返回 login=true 时 user 字段必定存在
    return {
        'user': data.get('user', ''),
        'is_admin': bool(data.get('is_admin', False)),
    }
       

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


    # 特殊节点（管理员专属）：非管理员拒绝，并做字段/大小校验
    sp = _parse_sp(content)
    if sp is not None:
        if not info.get('is_admin'):
            raise DnfsExp('特殊节点仅管理员可发布，用户'+info.get('user')+'，从'+request.remote_addr, 403)
        _validate_sp(sp)

    # 内容审查（特殊节点剔除 base64 负载）
    review = review_content(_sp_review_text(title, sp) if sp is not None else title + '\n' + content)
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
# [SECURITY-AUDIT] P0-3 / P1-10 —— 已修复
# 原问题：
#   · P0-3 delete_note / update_note 原缺 @_handle_exp 装饰器，异常冒泡成 Flask 默认 500 页。
#   · P1-10 上述函数内 decode_token 等调用无 try-except，异常直接裸抛 500。
# 修复：两函数均加 @_handle_exp（定义见 L294），DnfsExp → 对应状态码、
#       其余 Exception → 500，统一返回结构化 JSON，不再泄露栈信息。
@app.route('/delete_note', methods=['POST'])
@_handle_exp
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
# [SECURITY-AUDIT] P0-3 / P1-10 —— 同 delete_note，已加 @_handle_exp 统一异常（见上）。
@app.route('/update_note', methods=['POST'])
@_handle_exp
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

    # 特殊节点（管理员专属）：非管理员拒绝，并做字段/大小校验
    sp = _parse_sp(content)
    if sp is not None:
        if not info.get('is_admin'):
            logs.warning('dnfs', f'update_note 特殊节点非管理员拒绝 id={id}', extra={'user': info['user']})
            return jsonify({"error": '特殊节点仅管理员可发布'}), 403
        try:
            _validate_sp(sp)
        except DnfsExp as e:
            return jsonify({"error": str(e)}), e.get_status()

    # 内容审查（特殊节点剔除 base64 负载）
    review = review_content(_sp_review_text(title, sp) if sp is not None else title + '\n' + content)
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
