-- 补丁 002：Notes 表新增创建时间 / 更新时间列
-- 数据库类型: SQLite
-- 依赖: create_sqlite3.sql（Notes 表已存在）
-- 幂等性: ADD COLUMN 为一次性迁移，重复执行会报 "duplicate column name"，
--         请按迁移编号顺序执行一次。

-- 创建时间（新增节点时由 dnfs2 填入本地时间）
ALTER TABLE Notes ADD COLUMN created_at DATETIME;
-- 更新时间（新增/更新节点时由 dnfs2 填入本地时间）
ALTER TABLE Notes ADD COLUMN updated_at DATETIME;
