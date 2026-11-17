-- ============================================================
-- 审核工单表：举报（帖子/评论）+ 账号申诉（封禁）统合
-- 字段：id, reporter_id, target_type(post/comment/appeal), target_id,
--       category(违规/广告/色情/其他/申诉), description, status(pending/resolved/rejected),
--       created_at, handled_by, handled_at
-- 重复约束：同一用户对同一目标仅允许一条 pending 工单（应用层检查 + 索引辅助）
-- ============================================================
CREATE TABLE IF NOT EXISTS report_tickets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reporter_id INT NOT NULL COMMENT '提交人ID（举报者/申诉者）',
    target_type VARCHAR(20) NOT NULL COMMENT 'post/comment/appeal',
    target_id INT NOT NULL COMMENT '目标ID（帖子/评论/被申诉用户ID）',
    category VARCHAR(20) NOT NULL DEFAULT 'other' COMMENT '违规/广告/色情/其他/申诉',
    description VARCHAR(500) NOT NULL DEFAULT '' COMMENT '补充说明或申诉理由',
    status VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending/resolved/rejected',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    handled_by INT DEFAULT NULL COMMENT '处理管理员ID',
    handled_at DATETIME DEFAULT NULL,
    KEY idx_report_pending (status, created_at),
    KEY idx_report_reporter (reporter_id, target_type, target_id, status),
    KEY idx_report_target (target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
