#!/usr/bin/env python3





'''

Dendrachy Network File-System (dnfs)

树状网络文件系统



可能加入数据库加密功能。

'''

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
#from flask_sqlalchemy import SQLAlchemy
from waitress import serve
import os
import json
import urllib.request
import urllib.error
from typing import Dict, Any
from security_middleware import apply_security_headers

DB_SERVER_URL = 'http://127.0.0.1:8005/execute'
def call_db_service(url: str, payload: dict, timeout: int  = 10) -> dict:
    '''
    使用标准库向数据库服务发送 JSON 请求并返回解析后的响应。

    Args:
        url: 服务端点 URL (例如 'http://127.0.0.1:8005/execute')
        payload: 要发送的 JSON 数据字典
        timeout: 请求超时秒数

    Returns:
        dict: 服务返回的 JSON 响应

    Raises:
        urllib.error.URLError: 网络连接错误
        urllib.error.HTTPError: HTTP 状态错误 (4xx, 5xx)
        json.JSONDecodeError: 响应不是有效的 JSON
    '''

    data = json.dumps(payload).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        # HTTP错误，尝试读取错误响应体
        error_body = e.read().decode('utf-8', errors='replace')
        raise urllib.error.HTTPError(
            e.url, e.code, f'HTTP Error {e.code}: {error_body}', e.hdrs, e.fp
        ) from None
    except urllib.error.URLError as e:
        raise urllib.error.URLError(f'连接失败: {e.reason}') from None

REVIEW_SERVER_URL = 'http://127.0.0.1:8007/review'
def review_content(content: str, timeout: int = 10) -> Dict[str, Any]:
    """
    调用 AI 内容审核服务的 /review 接口，对给定文本进行审核。

    Args:
        content: 待审核的文本内容，不可为空。
        timeout: 请求超时时间（秒），默认 10 秒。

    Returns:
        字典，包含接口返回的字段，例如：
        - 成功时：{"invalid": "true"} 或 {"invalid": "false"}
        - API 错误时：{"invalid": "false", "error": "api_error", "detail": "..."}
        - 解析失败时：{"invalid": "false", "error": "parse_failed", "raw": "..."}
        - 请求参数错误时：{"error": "content is required"} 等
        - 网络或异常时：{"error": "request_failed", "detail": "..."}
    """
    url = REVIEW_SERVER_URL
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
            data =  json.loads(resp_data)
            invalid: bool = data['invalid']
            return invalid == False
            #invalid = data
            #return invalid

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



#basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
apply_security_headers(app)
CORS(app)

#app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'notes.db')

#app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

#db = SQLAlchemy(app)

#class Note(db.Model):

#    id = db.Column(db.Integer, primary_key=True)

#    pid = db.Column(db.Integer, nullable=False)

#    title = db.Column(db.String(128), nullable=False)

#    content = db.Column(db.Text, nullable=False)



#    def __repr__(self):

#        return f"<Node {self.title}>"

#with app.app_context():

#    db.create_all()



# eg: {
#    "pid": 0,
#    "title": "test",
#    "content": "hello"
#} 
@app.route('/add_note', methods=['POST'])

def add_note():

    data = request.get_json()

    pid: int = data.get('pid')

    title: str = data.get('title')

    content: str = data.get('content')

    if pid is None or not title or not content:

        return jsonify({"error": "Pid, title and content are required"}), 400

    #new_note = Note(pid=int(pid), title=title, content=content)
    
    #with app.app_context():

    #    db.session.add(new_note)

    #    db.session.commit()


    if pid != 0:

        select_payload = {
            'action': 'select',
            'table': 'Notes',
            'where': {
                'id': pid
            }
        }

        notes = call_db_service(DB_SERVER_URL, select_payload)

        # 不存在的父节点
        if len( notes['data']) == 0:
            return jsonify({'error': 'the new node need a parent'}), 400


    # 内容审查
    review = review_content(title + '\n' + content, 10)
    if True != review:
        #return jsonify({'error': 'review failed', 'data': review}), 400
        return jsonify({'error': 'review failed'}), 400

    insert_payload = {
        'action': 'insert',
        'table': 'Notes',
        'values': {
            "pid": pid,
            "title": title,
            'content': content
        }
    }

    result = call_db_service(DB_SERVER_URL, insert_payload)

    success = result['success']

    if success is True:
        return jsonify({'success': success}), 200
    else:
        return jsonify({'success': success}), 400


# {“pid”: 0} 
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

    notes = call_db_service(DB_SERVER_URL, select_payload)

    #with app.app_context():

    #    notes = Note.query.filter(Note.pid == int(pid))

    #result = [{
    #    'id': note['id'],
    #    'pid': note['pid'],
    #    'title': note['title'],
    #    'content':note['content']
    #} for note in notes]

    result = notes['data']

    return jsonify(result), 200


# eg: {"id": 1}
@app.route('/delete_note', methods=['POST'])

def delete_note():

    data = request.get_json()

    note_id: int = data.get('id')

    if not note_id:

        return jsonify({"error": "Note ID is required"}), 400

    #with app.app_context():

    #    notes = Note.query.filter(Note.pid == int(note_id))

    #    note = Note.query.get(note_id)

    #    if note:

    #        db.session.delete(note)

    #        db.session.commit()

    #        return jsonify({'message': "Note deleted successfully"}), 200

    #    else:

    #        return jsonify({"error": "Note not found"}), 404


    select_payload = {
        'action': 'select',
        'table': 'Notes',
        'where': {
            'pid': note_id
        }
    }

    notes = call_db_service(DB_SERVER_URL, select_payload)

    # 有子节点的情况不可删除
    if len(notes['data']) > 0:

        return jsonify({'error': 'this node has a child '}), 400



    delete_payload = {
        'action': 'delete',
        'table': 'Notes',
        'where': {'id': note_id}
    }

    result = call_db_service(DB_SERVER_URL, delete_payload)

    result = result['success']

    if result is True:

        return jsonify({'success': True}), 200

    else:

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

        return jsonify({"error": "Invalid param"}), 400


    if pid != 0:

        select_payload = {
            'action': 'select',
            'table': 'Notes',
            'where': {
                'id': pid
            }
        }

        notes = call_db_service(DB_SERVER_URL, select_payload)

        # 不存在的父节点
        if len( notes['data']) == 0:
            return jsonify({'error': 'the new node need a parent'}), 400


    # 内容审查
    review = review_content(title + '\n' + content, 10)
    if True != review:
        #return jsonify({'error': 'review failed', 'data': review}), 400
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

    result = call_db_service(DB_SERVER_URL, update_payload)

    result = result['success']

    if result is True:

        return jsonify({'success': True}), 200

    else:

        return jsonify({'success': False}), 400




#@app.route('/')
#def view_page():
#    """返回应用主页"""
#    return render_template('index.html')

ADDR='127.0.0.1'
PORT=8006
if __name__ == "__main__":
    print(f'Listen at http://{ADDR}:{PORT}')
    #app.run(host="127.0.0.1", port=8006)
    serve(app, host='127.0.0.1', port=8006)
