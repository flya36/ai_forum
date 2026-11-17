-- ============================================================
-- 板块申请审核表迁移脚本（针对已有环境）
-- 新增：
--   1. board_application 表（用户申请新板块 + 管理员审核）
--   2. board.board_name 唯一约束 uk_board_name
--      （当前 board 表 4 个默认板块名称互不重复，无历史重复数据，可安全执行；
--        若之后手工插入过重名板块，请先自行去重再执行本脚本）
-- 回滚方式：
--   ALTER TABLE board DROP INDEX uk_board_name;
--   DROP TABLE board_application;
-- 新环境无需执行本脚本，create_tables.sql 已同步建表与唯一约束。
-- ============================================================

USE `ai_forum`;

-- 1. 板块申请表（user_id 级联删除：用户注销则其申请记录一并删除；
--    reviewer_id SET NULL：管理员删除后保留申请记录与审核结果）
CREATE TABLE IF NOT EXISTS `board_application` (
  `application_id` INT          NOT NULL AUTO_INCREMENT COMMENT '申请ID，主键',
  `user_id`        INT          NOT NULL                COMMENT '申请人ID，外键→user.user_id',
  `board_name`     VARCHAR(50)  NOT NULL                COMMENT '申请的板块名称',
  `description`    VARCHAR(255) DEFAULT NULL            COMMENT '板块描述/申请说明',
  `status`         TINYINT      NOT NULL DEFAULT 0      COMMENT '状态：0=待审核，1=已通过，2=已驳回',
  `apply_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '申请时间',
  `review_time`    DATETIME     DEFAULT NULL            COMMENT '审核时间',
  `reviewer_id`    INT          DEFAULT NULL            COMMENT '审核管理员ID，外键→user.user_id',
  `reject_reason`  VARCHAR(255) DEFAULT NULL            COMMENT '驳回原因',
  PRIMARY KEY (`application_id`),
  KEY `idx_app_user`       (`user_id`),
  KEY `idx_app_status`     (`status`),
  KEY `idx_app_board_name` (`board_name`),
  CONSTRAINT `fk_app_user`     FOREIGN KEY (`user_id`)     REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_app_reviewer` FOREIGN KEY (`reviewer_id`) REFERENCES `user` (`user_id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='板块申请审核表';

-- 2. board 表名称唯一约束（防重名板块；唯一索引同时兜底并发创建同名板块）
ALTER TABLE `board`
  ADD UNIQUE KEY `uk_board_name` (`board_name`);

SELECT 'board_application 表创建完成，board.board_name 唯一约束添加完成' AS result;
