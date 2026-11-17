-- 站内私信：messages 表（对话线程用组合查询，不建 conversations 表）
CREATE TABLE IF NOT EXISTS messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sender_id INT NOT NULL,
  receiver_id INT NOT NULL,
  content TEXT NOT NULL,
  is_read TINYINT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_msg_receiver (receiver_id, is_read, created_at),
  INDEX idx_msg_sender (sender_id, created_at),
  INDEX idx_msg_pair (sender_id, receiver_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
