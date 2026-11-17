-- 用户关注关系表（幂等迁移）
-- 单向关注：follower_id 关注 followee_id
-- 唯一索引防重复关注（并发 INSERT 兜底）
CREATE TABLE IF NOT EXISTS user_follows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    follower_id INT NOT NULL COMMENT '关注者 user_id',
    followee_id INT NOT NULL COMMENT '被关注者 user_id',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_follow (follower_id, followee_id),
    KEY idx_followee (followee_id),
    KEY idx_follower (follower_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户关注关系';
