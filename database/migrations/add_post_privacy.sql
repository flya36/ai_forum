-- ============================================================
-- 新增字段：帖子私密/公开、匿名/本名
-- 在已有 ai_forum 库中执行此脚本
-- ============================================================

USE `ai_forum`;

-- 帖子表加字段
ALTER TABLE `post`
  ADD COLUMN `is_private`   TINYINT NOT NULL DEFAULT 0 COMMENT '是否私密：0=公开，1=私密' AFTER `is_essence`,
  ADD COLUMN `is_anonymous` TINYINT NOT NULL DEFAULT 0 COMMENT '是否匿名：0=本名，1=匿名' AFTER `is_private`;

-- 加索引（私密过滤时加速）
ALTER TABLE `post` ADD INDEX `idx_private` (`is_private`);

SELECT '字段添加完成：is_private, is_anonymous' AS result;
