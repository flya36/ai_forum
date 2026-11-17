-- ============================================================
-- 修复 notification.post_id 可空性（针对已有环境）
--
-- 背景：create_tables.sql 最新版声明 post_id INT DEFAULT NULL
--       （帖子删除时 ON DELETE SET NULL，通知本身保留），但已有环境
--       的历史建表把 post_id 定为 NOT NULL，导致 add_notification
--       传入 post_id=None（如板块申请审核通知）时报
--       "Column 'post_id' cannot be null" 而静默失败。
-- 本迁移将已有环境的该列改为可空，与最新建表脚本对齐。
-- 回滚方式：
--   ALTER TABLE notification MODIFY post_id INT NOT NULL;
--   （注意：回滚前需确认不存在 post_id IS NULL 的历史行）
-- ============================================================

USE `ai_forum`;

ALTER TABLE `notification`
  MODIFY `post_id` INT DEFAULT NULL COMMENT '相关帖子ID（帖子删除后置空）';

SELECT 'notification.post_id 已改为可空' AS result;
