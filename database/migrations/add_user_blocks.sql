-- 拉黑功能：user_blocks（单数表名），唯一索引防重复拉黑
CREATE TABLE IF NOT EXISTS user_blocks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  blocker_id INT NOT NULL,
  blocked_id INT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_block_pair (blocker_id, blocked_id),
  INDEX idx_block_blocked (blocked_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
