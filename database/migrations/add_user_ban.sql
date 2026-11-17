-- ============================================================
-- 用户封禁：为 user 表补充封禁字段
-- 说明：封禁标志复用现有 status 字段（0=正常，1=封禁），
--       ban_reason 记录原因，ban_until 记录到期时间（NULL=永久）。
-- 幂等：先检查列是否存在，存在则跳过。
-- ============================================================
SET @db_name = DATABASE();

-- ban_reason
SET @col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db_name AND TABLE_NAME = 'user' AND COLUMN_NAME = 'ban_reason'
);
SET @sql = IF(@col_exists = 0,
  'ALTER TABLE `user` ADD COLUMN `ban_reason` VARCHAR(255) DEFAULT NULL COMMENT ''封禁原因'' AFTER `status`',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ban_until
SET @col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db_name AND TABLE_NAME = 'user' AND COLUMN_NAME = 'ban_until'
);
SET @sql = IF(@col_exists = 0,
  'ALTER TABLE `user` ADD COLUMN `ban_until` DATETIME DEFAULT NULL COMMENT ''封禁到期时间，NULL=永久'' AFTER `ban_reason`',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
