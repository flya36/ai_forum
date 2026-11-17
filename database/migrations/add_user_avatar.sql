-- ============================================================
-- 给 user 表增加头像路径字段
-- 在已有 ai_forum 库中执行
-- ============================================================
USE `ai_forum`;

ALTER TABLE `user`
  ADD COLUMN `avatar_path` VARCHAR(255) DEFAULT NULL COMMENT '头像图片文件名'
  AFTER `signature`;
