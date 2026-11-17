-- ============================================================
-- 新增表：点赞记录表 + 通知表
-- 在已有 ai_forum 库中执行此脚本即可
-- ============================================================

USE `ai_forum`;

-- 1. 点赞记录表：防止重复点赞，支持取消点赞
CREATE TABLE IF NOT EXISTS `post_like` (
  `id`        INT      NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id`   INT      NOT NULL COMMENT '点赞用户ID',
  `post_id`   INT      NOT NULL COMMENT '被点赞帖子ID',
  `like_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '点赞时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_post` (`user_id`, `post_id`),
  KEY `idx_post` (`post_id`),
  CONSTRAINT `fk_like_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_like_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`post_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='帖子点赞记录表';

-- 2. 通知表：点赞/评论时给帖主发通知
CREATE TABLE IF NOT EXISTS `notification` (
  `id`           INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id`      INT          NOT NULL COMMENT '接收通知的用户ID（帖主）',
  `type`         VARCHAR(20)  NOT NULL COMMENT '通知类型：like=点赞 / comment=评论',
  `post_id`      INT          NOT NULL COMMENT '相关帖子ID',
  `from_user_id` INT          NOT NULL COMMENT '触发通知的用户ID',
  `content`      VARCHAR(255) DEFAULT NULL COMMENT '通知内容摘要',
  `is_read`      TINYINT      NOT NULL DEFAULT 0 COMMENT '是否已读：0=未读，1=已读',
  `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '通知时间',
  PRIMARY KEY (`id`),
  KEY `idx_user` (`user_id`),
  KEY `idx_unread` (`user_id`, `is_read`),
  CONSTRAINT `fk_notify_user`  FOREIGN KEY (`user_id`)      REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notify_from`  FOREIGN KEY (`from_user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notify_post`  FOREIGN KEY (`post_id`)      REFERENCES `post` (`post_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='系统通知表';
