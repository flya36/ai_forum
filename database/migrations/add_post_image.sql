-- 帖子表增加图片字段（每帖一张主图，表情包/配图）
ALTER TABLE post ADD COLUMN image_path VARCHAR(255) DEFAULT NULL;
