-- 补丁 003：Users 管理员标记 + Notes 可见性/可编辑性
-- 数据库类型: SQLite
-- 依赖: create_sqlite3.sql（或等价 schema）已执行，且 patch_001 / patch_002 已按序执行
-- 幂等性: ADD COLUMN 为一次性迁移，重复执行会报 "duplicate column name"，
--         请按迁移编号顺序执行一次。
--
-- 语义（默认值向后兼容现有行为）：
--   Users.is_admin           是否管理员，默认 0（普通用户）
--   Notes.is_public          节点是否公开可读，默认 1（公开，与现状一致）
--   Notes.everyone_editable  是否任何人都能编辑，默认 1（人人可编辑）

-- ── Users 新增管理员标记 ─────────────────────────────────────────────
ALTER TABLE Users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0;

-- ── Notes 新增可见性 / 可编辑性 ──────────────────────────────────────
ALTER TABLE Notes ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE Notes ADD COLUMN everyone_editable BOOLEAN NOT NULL DEFAULT 1;
