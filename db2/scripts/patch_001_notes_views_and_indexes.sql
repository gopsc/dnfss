-- 补丁 001：Notes 表新增观看人数列 + 补齐索引
-- 数据库类型: SQLite
-- 依赖: create_sqlite3.sql 已执行（Users / Notes / Logs 表已存在）
-- 幂等性: 索引部分可重复执行（IF NOT EXISTS）；
--         ADD COLUMN 为一次性迁移，重复执行会报 "duplicate column name: views"，
--         请按迁移编号顺序执行一次。

-- ── Notes 新增观看人数列 ────────────────────────────────────────────
-- 观看人数（view count），非空、默认 0，保证旧数据与新插入兼容。
ALTER TABLE Notes ADD COLUMN views INTEGER NOT NULL DEFAULT 0;

-- ── 补齐此前缺失的索引 ──────────────────────────────────────────────
-- dnfs2 get_notes 按 pid 查询子节点，此前为全表扫描。
CREATE INDEX IF NOT EXISTS idx_notes_pid   ON Notes(pid);
-- dnfs2 / ca 按 owner 做所有权校验，此前为全表扫描。
CREATE INDEX IF NOT EXISTS idx_notes_owner ON Notes(owner);
