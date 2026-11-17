-- ============================================================
-- 标签表迁移脚本（针对已有环境）
-- 新增：tag.created_at（标签创建时间）
--
-- 理由：用户需求要求 tag 表补充 created_at 字段，用于
--       管理端标签列表的创建时间展示与排序。
-- 已有环境执行本脚本即可；新环境在 create_tables.sql 中已同步该列。
-- ============================================================

USE `ai_forum`;

ALTER TABLE `tag`
  ADD COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '标签创建时间' AFTER `tag_name`;

SELECT 'tag 表迁移完成：新增 created_at 列' AS result;
