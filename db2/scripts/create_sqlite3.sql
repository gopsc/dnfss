-- Users 表创建脚本
-- 数据库类型: SQLite

CREATE TABLE IF NOT EXISTS Users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    create_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid INTEGER NOT NULL,
    owner VARCHAR(128) DEFAULT 'Unknow',
    title VARCHAR(128) NOT NULL,
    content TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME,
    updated_at DATETIME
);

-- 旧版增量
-- ALTER TABLE Notes ADD COLUMN owner VARCHAR(128) DEFAULT 'Unknow';
-- ALTER TABLE Notes ADD COLUMN views INTEGER NOT NULL DEFAULT 0;       -- patch_001 观看人数列
-- ALTER TABLE Notes ADD COLUMN created_at DATETIME;                     -- patch_002 创建时间
-- ALTER TABLE Notes ADD COLUMN updated_at DATETIME;                     -- patch_002 更新时间

-- Notes 索引：get_notes 按 pid 查子节点、所有权校验按 owner（patch_001 并入）
CREATE INDEX IF NOT EXISTS idx_notes_pid   ON Notes(pid);
CREATE INDEX IF NOT EXISTS idx_notes_owner ON Notes(owner);

-- ── 日志表（db 内置日志模块）─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS Logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    service   TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    message   TEXT    NOT NULL,
    trace_id  TEXT,
    extra     TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_service_ts ON Logs(service, ts);
CREATE INDEX IF NOT EXISTS idx_logs_level_ts   ON Logs(level, ts);
CREATE INDEX IF NOT EXISTS idx_logs_ts          ON Logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_trace       ON Logs(trace_id);
