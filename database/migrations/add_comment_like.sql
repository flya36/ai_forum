-- ============================================================
-- 评论点赞表（comment_like）
-- 说明：与 post_like 结构对齐；唯一索引防重复点赞；外键级联删除
-- 依赖：comment / user 表存在
-- ============================================================
CREATE TABLE IF NOT EXISTS `comment_like` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id` int NOT NULL COMMENT '点赞用户ID',
  `comment_id` int NOT NULL COMMENT '被点赞评论ID',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '点赞时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_comment` (`user_id`,`comment_id`),
  KEY `idx_comment` (`comment_id`),
  CONSTRAINT `fk_clike_comment` FOREIGN KEY (`comment_id`) REFERENCES `comment` (`comment_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_clike_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='评论点赞记录表'
