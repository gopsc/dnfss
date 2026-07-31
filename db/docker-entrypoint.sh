#!/bin/bash
set -e

cd "$(dirname "$0")"

DATA_DIR="/app/data"
DB_PATH="${DATABASE_URL#sqlite:///}"
if [ "$DB_PATH" = "$DATABASE_URL" ]; then
    # URL 不是 sqlite:/// 格式，使用默认路径
    DB_PATH="$DATA_DIR/database.db"
fi

echo "数据目录: $DATA_DIR"
echo "数据库路径: $DB_PATH"

# 确保数据目录存在并可写
mkdir -p "$(dirname "$DB_PATH")"

# 初始化数据库表（如果不存在）
if [[ "$DATABASE_URL" == sqlite:* ]]; then
    echo "初始化数据库 schema..."
    python -c "
import os
from sqlalchemy import create_engine, text

db_url = os.environ.get('DATABASE_URL', 'sqlite:////app/data/database.db')
engine = create_engine(
    db_url,
    connect_args={'check_same_thread': False} if db_url.startswith('sqlite') else {},
)
# 应用 SQLite PRAGMA 优化
from sqlalchemy import event
@event.listens_for(engine, 'connect')
def _configure_sqlite(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL')
    cur.execute('PRAGMA synchronous=NORMAL')
    cur.execute('PRAGMA foreign_keys=ON')
    cur.execute('PRAGMA busy_timeout=5000')
    cur.close()

with open('create_sqlite3.sql', 'r') as f:
    schema_sql = f.read()
with engine.connect() as conn:
    for stmt in schema_sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                pass
    conn.commit()
print('Schema 初始化完成.')
"
fi

echo "启动 dbv2 服务 ${LISTEN_ADDR:-0.0.0.0}:${LISTEN_PORT:-8005}..."
exec python dbv2.py --addr "${LISTEN_ADDR:-0.0.0.0}" --port "${LISTEN_PORT:-8005}" --workers "${WORKERS:-4}"
