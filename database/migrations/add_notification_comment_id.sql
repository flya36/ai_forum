-- @提及通知跳转：notification 表增加 comment_id 列（评论提及可跳转 #comment-锚点）
-- 幂等：列已存在时不重复添加
SET @col_exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification' AND COLUMN_NAME = 'comment_id');
SET @ddl := IF(@col_exists = 0,
    'ALTER TABLE notification ADD COLUMN comment_id INT NULL AFTER post_id',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
