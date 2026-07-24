import os
from flask import Flask, request, jsonify, g
from sqlalchemy import (
    create_engine, MetaData, Table, select, insert, update, delete, text
)
from sqlalchemy.exc import SQLAlchemyError
from waitress import serve
from security_middleware import apply_security_headers

app = Flask(__name__)
apply_security_headers(app)

# 数据库配置
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
engine = create_engine(DATABASE_URL)

# 缓存反射的元数据（避免每次请求都反射）
metadata = MetaData()

def get_table(table_name):
    """反射表对象（若已缓存则直接返回）"""
    if table_name not in metadata.tables:
        Table(table_name, metadata, autoload_with=engine)
    return metadata.tables[table_name]

def build_where(table, conditions):
    """将条件字典转换为 SQLAlchemy 条件表达式"""
    if not conditions:
        return None
    clauses = []
    for col, val in conditions.items():
        column = table.columns[col]
        if isinstance(val, dict):
            # 支持多种比较操作
            if 'eq' in val:
                clauses.append(column == val['eq'])
            if 'ne' in val:
                clauses.append(column != val['ne'])
            if 'gt' in val:
                clauses.append(column > val['gt'])
            if 'lt' in val:
                clauses.append(column < val['lt'])
            if 'ge' in val:
                clauses.append(column >= val['ge'])
            if 'le' in val:
                clauses.append(column <= val['le'])
            if 'like' in val:
                clauses.append(column.like(val['like']))
            if 'in' in val:
                clauses.append(column.in_(val['in']))
        else:
            # 直接等值比较
            clauses.append(column == val)
    from sqlalchemy import and_
    return and_(*clauses)

@app.route('/execute', methods=['POST'])
def execute_structured():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体不能为空'}), 400

    action = data.get('action', 'select')
    table_name = data.get('table')
    if not table_name:
        return jsonify({'success': False, 'error': '缺少 table 字段'}), 400

    try:
        table = get_table(table_name)
    except Exception as e:
        return jsonify({'success': False, 'error': f'表加载失败: {str(e)}'}), 400

    conn = None
    try:
        conn = engine.connect()
        result = []
        if action == 'select':
            columns = data.get('columns', None)
            if columns:
                cols = [table.columns[c] for c in columns]
            else:
                cols = [table]   # select *

            stmt = select(*cols)
            where_cond = build_where(table, data.get('where'))
            if where_cond is not None:
                stmt = stmt.where(where_cond)

            # 排序
            order_by = data.get('order_by')
            if order_by:
                for item in order_by:
                    col = table.columns[item['column']]
                    stmt = stmt.order_by(col.desc() if item.get('desc') else col.asc())

            # 分页
            limit = data.get('limit')
            if limit is not None:
                stmt = stmt.limit(limit)
            offset = data.get('offset')
            if offset is not None:
                stmt = stmt.offset(offset)

            res = conn.execute(stmt)
            rows = res.fetchall()
            result = [dict(row._mapping) for row in rows]

        elif action == 'insert':
            values = data.get('values', {})
            stmt = insert(table).values(**values)
            res = conn.execute(stmt)
            conn.commit()
            result = [{'lastrowid': res.lastrowid}] if hasattr(res, 'lastrowid') else []

        elif action == 'update':
            values = data.get('values', {})
            where_cond = build_where(table, data.get('where'))
            stmt = update(table).values(**values)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            res = conn.execute(stmt)
            conn.commit()
            result = [{'rowcount': res.rowcount}]

        elif action == 'delete':
            where_cond = build_where(table, data.get('where'))
            stmt = delete(table)
            if where_cond is not None:
                stmt = stmt.where(where_cond)
            res = conn.execute(stmt)
            conn.commit()
            result = [{'rowcount': res.rowcount}]

        else:
            return jsonify({'success': False, 'error': f'不支持的操作: {action}'}), 400

        return jsonify({'success': True, 'data': result})

    except SQLAlchemyError as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    # 单线程单进程，绑定 127.0.0.1
    serve(app, host='127.0.0.1', port=8005, threads=1)
