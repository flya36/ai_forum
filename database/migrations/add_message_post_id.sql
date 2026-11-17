-- 社交关系增强(2/2)：帖子分享 —— message 表增加 post_id（INT NULL + 索引）
ALTER TABLE message ADD COLUMN post_id INT NULL AFTER content;
ALTER TABLE message ADD INDEX idx_message_post_id (post_id);
