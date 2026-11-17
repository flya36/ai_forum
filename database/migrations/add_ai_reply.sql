-- AI 智能回复（幂等迁移）
-- 1. posts 表加 ai_replied 标记（防重复回复）
SET @exist := (SELECT COUNT(*) FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'post' AND column_name = 'ai_replied');
SET @sql := IF(@exist = 0,
    'ALTER TABLE post ADD COLUMN ai_replied TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否已触发AI回复 0=否 1=是'' AFTER is_anonymous',
    'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. 全局配置表（AI 回复开关等）
CREATE TABLE IF NOT EXISTS app_settings (
    skey   VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '配置键',
    svalue VARCHAR(255) NOT NULL DEFAULT '' COMMENT '配置值'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局配置';

-- 默认开启 AI 智能回复（管理员可在后台关闭）
INSERT INTO app_settings (skey, svalue) VALUES ('ai_reply_enabled', '1')
ON DUPLICATE KEY UPDATE skey = VALUES(skey);

-- 每日最大回复数（默认 50，管理员可调；0 = 禁用）
INSERT INTO app_settings (skey, svalue) VALUES ('ai_reply_daily_limit', '50')
ON DUPLICATE KEY UPDATE skey = VALUES(skey);
