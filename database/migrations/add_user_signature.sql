-- ============================================================
-- 给 user 表增加个性签名字段
-- 在已有 ai_forum 库中执行
-- ============================================================
USE `ai_forum`;

ALTER TABLE `user`
  ADD COLUMN `signature` VARCHAR(255) DEFAULT NULL COMMENT '个性签名'
  AFTER `nickname`;
