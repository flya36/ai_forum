-- ============================================================
-- 补充关系外键：user_follows / user_blocks / message / report_tickets
-- 背景：这些表初建时仅建普通索引，Workbench E-R 图不显示关系线；
--       2026-09-18 清理 29 行孤儿数据后补齐外键约束。
-- 前置步骤（已执行，孤儿数据已清零）：
--   DELETE ub FROM user_blocks ub LEFT JOIN `user` u ON ub.blocker_id=u.user_id WHERE u.user_id IS NULL;
--   DELETE ub FROM user_blocks ub LEFT JOIN `user` u ON ub.blocked_id=u.user_id WHERE u.user_id IS NULL;
--   DELETE rt FROM report_tickets rt LEFT JOIN `user` u ON rt.reporter_id=u.user_id WHERE u.user_id IS NULL;
--   DELETE rt FROM report_tickets rt LEFT JOIN `user` u ON rt.handled_by=u.user_id WHERE u.user_id IS NULL;
-- 说明：
--   - audit_log 有意不加外键（审计只增不改、防级联篡改）；
--   - report_tickets.target_id 为多态关联（post/comment/appeal），无法加单一外键。
-- ============================================================
USE `ai_forum`;

ALTER TABLE user_follows
  ADD CONSTRAINT fk_follow_follower FOREIGN KEY (follower_id) REFERENCES `user`(user_id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_follow_followee FOREIGN KEY (followee_id) REFERENCES `user`(user_id) ON DELETE CASCADE;

ALTER TABLE user_blocks
  ADD CONSTRAINT fk_block_blocker FOREIGN KEY (blocker_id) REFERENCES `user`(user_id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_block_blocked FOREIGN KEY (blocked_id) REFERENCES `user`(user_id) ON DELETE CASCADE;

ALTER TABLE message
  ADD CONSTRAINT fk_msg_sender   FOREIGN KEY (sender_id)   REFERENCES `user`(user_id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_msg_receiver FOREIGN KEY (receiver_id) REFERENCES `user`(user_id) ON DELETE CASCADE;

ALTER TABLE report_tickets
  ADD CONSTRAINT fk_ticket_reporter FOREIGN KEY (reporter_id) REFERENCES `user`(user_id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_ticket_handler  FOREIGN KEY (handled_by)  REFERENCES `user`(user_id) ON DELETE SET NULL;
