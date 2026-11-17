-- ============================================================
-- 审计日志表（管理员/敏感操作审计）
-- 使用：mysql -u root -p ai_forum < database/migrations/add_audit_log.sql
-- 权限：仅应用账号 INSERT + SELECT；禁止 UPDATE/DELETE（防篡改）
-- ============================================================
USE `ai_forum`;

CREATE TABLE IF NOT EXISTS `audit_log` (
  `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '审计ID，主键',
  `operator_id` INT          NOT NULL                COMMENT '操作者用户ID',
  `action`      VARCHAR(50)  NOT NULL                COMMENT '操作类型（白名单）',
  `target_type` VARCHAR(30)  DEFAULT NULL            COMMENT '目标类型：user/post/comment/tag/board/board_application',
  `target_id`   INT          DEFAULT NULL            COMMENT '目标ID',
  `detail`      VARCHAR(500) DEFAULT NULL            COMMENT '附加说明（脱敏后写入，禁止明文隐私）',
  `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
  PRIMARY KEY (`id`),
  KEY `idx_audit_operator` (`operator_id`),
  KEY `idx_audit_action`   (`action`),
  KEY `idx_audit_time`     (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='敏感操作审计日志（只增不改）';
