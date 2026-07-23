import sqlite3
from flask import Flask, request, jsonify, g
from waitress import serve

app = Flask(__name__)
app.config['DATABASE'] = 'database.db'

def get_db():
    if 'db' not in g:
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.route('/execute', methods=['POST'])
def execute_sql():
    data = request.get_json()
    if not data or 'sql' not in data:
        return jsonify({'success': False, 'error': '缺少 sql 字段'}), 400

    sql = data['sql'].strip()
    params = data.get('params', [])

    # 参数必须是列表、元组或字典
    if not isinstance(params, (list, tuple, dict)):
        params = []

    try:
        db = get_db()
        cur = db.execute(sql, params)
        db.commit()

        try:
            rows = cur.fetchall()
            result = [dict(row) for row in rows]
        except Exception:
            result = []

        return jsonify({
            'success': True,
            'data': result
        })
    except Exception as e:
        db = g.get('db')
        if db:
            db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400

if __name__ == '__main__':
    # 单线程单进程，绑定 127.0.0.1:5000
    serve(app, host='127.0.0.1', port=5000, threads=1)