-- 补丁 004：Notes 可见性/可编辑性默认值收紧
-- 数据库类型: SQLite
-- 依赖: patch_003 已执行（Notes.is_public / Notes.everyone_editable 列已存在）
-- 幂等性: UPDATE 可重复执行，结果一致。
--
-- 语义（dnfs2 自本补丁起强制以下权限）：
--   Notes.is_public          节点是否公开可读，1=所有人可见，0=仅拥有者/管理员可见
--   Notes.everyone_editable  是否允许他人在该节点下添加子节点，
--                            1=任何登录用户可添加，0=仅拥有者/管理员可添加
--   根节点（pid=0）所有人可见，仅管理员可添加子节点（dnfs2 代码强制，与列值无关）
--
-- 存量节点全部设为「公开 + 禁止他人添加子节点」；
-- 新建节点由 dnfs2 显式写入 is_public=1 / everyone_editable=0（前端编辑框可改）。
-- 注意: SQLite 不支持 ALTER COLUMN DEFAULT，列默认值仍为 patch_003 的 1，
--       但所有写入方（dnfs2）均显式赋值，列默认值不再影响业务。

UPDATE Notes SET is_public = 1, everyone_editable = 0;