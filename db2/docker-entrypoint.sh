#!/bin/bash
set -e

# 从 DATABASE_URL 提取 SQLite 文件路径并确保目录存在
if [[ "${DATABASE_URL:-}" == sqlite:///* ]]; then
    DB_PATH="${DATABASE_URL#sqlite:///}"
    mkdir -p "$(dirname "$DB_PATH")"
fi

# 初始化 schema（幂等）
if [[ -n "${DATABASE_URL:-}" ]]; then
    DB_FILE="${DATABASE_URL#sqlite:///}"
    sqlite3 "$DB_FILE" <<'SQL'
CREATE TABLE IF NOT EXISTS Users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    create_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login    DATETIME,
    is_active     BOOLEAN DEFAULT 1
);
CREATE TABLE IF NOT EXISTS Notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pid     INTEGER NOT NULL,
    owner   VARCHAR(128) DEFAULT 'Unknow',
    title   VARCHAR(128) NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS Logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    service  TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL,
    trace_id TEXT,
    extra    TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_service_ts ON Logs(service, ts);
CREATE INDEX IF NOT EXISTS idx_logs_level_ts   ON Logs(level, ts);
CREATE INDEX IF NOT EXISTS idx_logs_ts         ON Logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_trace      ON Logs(trace_id);
SQL
fi

exec /app/db2 \
    --addr "${LISTEN_ADDR:-0.0.0.0}" \
    --port "${LISTEN_PORT:-8005}" \
    --workers "${WORKERS:-4}"
