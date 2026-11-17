-- ============================================================
-- 通知表迁移脚本（针对已有环境）
-- 修复：管理员删除帖子后无法给帖主发删除通知的问题
--
-- 原因：原 notification.post_id 是 NOT NULL 且 ON DELETE CASCADE，
--       帖子删除时相关通知被级联删除，导致"先删帖再发通知"失败。
--
-- 变更：post_id 改为可空（DEFAULT NULL），外键改为 ON DELETE SET NULL，
--       帖子删除后通知保留，post_id 置空。
--
-- 注意：MySQL 无法直接修改外键约束，需先删后重建。
-- ============================================================

USE `ai_forum`;

-- 1. 删除旧外键约束（约束名取自原 add_tables.sql）
ALTER TABLE `notification` DROP FOREIGN KEY `fk_notify_post`;

-- 2. post_id 改为可空
ALTER TABLE `notification`
  MODIFY COLUMN `post_id` INT DEFAULT NULL COMMENT '相关帖子ID（帖子删除后置空）';

-- 3. 重建外键，改为 ON DELETE SET NULL
ALTER TABLE `notification`
  ADD CONSTRAINT `fk_notify_post` FOREIGN KEY (`post_id`)
  REFERENCES `post` (`post_id`) ON DELETE SET NULL;

SELECT '通知表迁移完成：post_id 可空 + ON DELETE SET NULL' AS result;
