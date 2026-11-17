-- ============================================================
-- 基于RAG的AI增强型知识社区系统 — 完整建表脚本（最新版）
-- 数据库：ai_forum
-- 字符集：utf8mb4    引擎：InnoDB
-- 建表顺序：无外键表 → 有外键表 → 中间表
--
-- 本脚本包含全部字段与表，可直接在新环境一次性建库，
-- 无需再逐个执行 add_*.sql 增量脚本。
-- ============================================================

USE `ai_forum`;

-- ============================================================
-- 第一批：无外键依赖的表（先建这些）
-- ============================================================

-- 1. 用户表
CREATE TABLE IF NOT EXISTS `user` (
  `user_id`       INT          NOT NULL AUTO_INCREMENT COMMENT '用户ID，主键',
  `username`      VARCHAR(50)  NOT NULL                COMMENT '登录用户名',
  `password_hash` VARCHAR(255) NOT NULL                COMMENT '密码哈希（不存明文，pbkdf2_sha256 格式）',
  `nickname`      VARCHAR(50)  DEFAULT NULL            COMMENT '显示昵称',
  `signature`     VARCHAR(255) DEFAULT NULL            COMMENT '个性签名',
  `avatar_path`   VARCHAR(255) DEFAULT NULL            COMMENT '头像图片文件名（存于 code/avatars/）',
  `role`          TINYINT      NOT NULL DEFAULT 0      COMMENT '角色：0=普通用户，1=管理员',
  `status`        TINYINT      NOT NULL DEFAULT 0      COMMENT '状态：0=正常，1=禁用',
  `create_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户表';

-- 2. 板块表
CREATE TABLE IF NOT EXISTS `board` (
  `board_id`    INT          NOT NULL AUTO_INCREMENT COMMENT '板块ID，主键',
  `board_name`  VARCHAR(50)  NOT NULL                COMMENT '板块名称',
  `description` VARCHAR(255) DEFAULT NULL            COMMENT '板块描述',
  `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`board_id`),
  UNIQUE KEY `uk_board_name` (`board_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='板块（话题分类）表';

-- 2.1 板块申请表（用户申请新板块 + 管理员审核）
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

-- 3. 标签表
CREATE TABLE IF NOT EXISTS `tag` (
  `tag_id`     INT         NOT NULL AUTO_INCREMENT COMMENT '标签ID，主键',
  `tag_name`   VARCHAR(50) NOT NULL                COMMENT '标签名称',
  `created_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '标签创建时间',
  PRIMARY KEY (`tag_id`),
  UNIQUE KEY `uk_tag_name` (`tag_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='话题标签表';

-- ============================================================
-- 第二批：有外键依赖的表（依赖第一批）
-- ============================================================

-- 4. 帖子表
CREATE TABLE IF NOT EXISTS `post` (
  `post_id`      INT          NOT NULL AUTO_INCREMENT COMMENT '帖子ID，主键',
  `title`        VARCHAR(200) NOT NULL                COMMENT '帖子标题',
  `content`      TEXT         NOT NULL                COMMENT '帖子正文',
  `author_id`    INT          NOT NULL                COMMENT '作者ID，外键→user.user_id',
  `board_id`     INT          NOT NULL                COMMENT '所属板块ID，外键→board.board_id',
  `view_count`   INT          NOT NULL DEFAULT 0      COMMENT '浏览量',
  `like_count`   INT          NOT NULL DEFAULT 0      COMMENT '点赞数',
  `is_top`       TINYINT      NOT NULL DEFAULT 0      COMMENT '是否置顶：0=否，1=是',
  `is_essence`   TINYINT      NOT NULL DEFAULT 0      COMMENT '是否加精：0=否，1=是',
  `is_private`   TINYINT      NOT NULL DEFAULT 0      COMMENT '是否私密：0=公开，1=私密（仅作者可见）',
  `is_anonymous` TINYINT      NOT NULL DEFAULT 0      COMMENT '是否匿名：0=本名，1=匿名',
  `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布时间',
  `update_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`post_id`),
  KEY `idx_author_id`   (`author_id`),
  KEY `idx_board_id`    (`board_id`),
  KEY `idx_create_time` (`create_time`),
  KEY `idx_private`     (`is_private`),
  CONSTRAINT `fk_post_author` FOREIGN KEY (`author_id`) REFERENCES `user`  (`user_id`)  ON DELETE CASCADE,
  CONSTRAINT `fk_post_board`  FOREIGN KEY (`board_id`)  REFERENCES `board` (`board_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='帖子表';

-- 5. 评论表
CREATE TABLE IF NOT EXISTS `comment` (
  `comment_id`  INT      NOT NULL AUTO_INCREMENT COMMENT '评论ID，主键',
  `post_id`     INT      NOT NULL                COMMENT '所属帖子ID，外键→post.post_id',
  `user_id`     INT      NOT NULL                COMMENT '评论者ID，外键→user.user_id',
  `content`     TEXT     NOT NULL                COMMENT '评论内容',
  `floor`       INT      NOT NULL DEFAULT 1      COMMENT '楼层号（已废弃，楼层由查询时 ROW_NUMBER() 动态计算）',
  `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '评论时间',
  PRIMARY KEY (`comment_id`),
  KEY `idx_comment_post` (`post_id`),
  KEY `idx_comment_user` (`user_id`),
  CONSTRAINT `fk_comment_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`post_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_comment_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='评论表';

-- 6. 知识库分块表
CREATE TABLE IF NOT EXISTS `knowledge_chunk` (
  `chunk_id`      INT      NOT NULL AUTO_INCREMENT COMMENT '分块ID，主键',
  `post_id`       INT      NOT NULL                COMMENT '所属帖子ID，外键→post.post_id',
  `chunk_content` TEXT     NOT NULL                COMMENT '分块文本内容',
  `chunk_index`   INT      NOT NULL DEFAULT 0      COMMENT '分块序号（同一帖子内从0开始）',
  `vector_id`     INT      DEFAULT NULL            COMMENT 'FAISS向量库中的索引ID',
  `update_time`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`chunk_id`),
  KEY `idx_chunk_post` (`post_id`),
  CONSTRAINT `fk_chunk_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`post_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='RAG知识库文本分块表';

-- ============================================================
-- 第三批：多对多中间表（依赖前两批）
-- ============================================================

-- 7. 收藏表（用户 M:N 帖子）
CREATE TABLE IF NOT EXISTS `favorite` (
  `fav_id`   INT      NOT NULL AUTO_INCREMENT COMMENT '收藏记录ID，主键',
  `user_id`  INT      NOT NULL                COMMENT '用户ID，外键→user.user_id',
  `post_id`  INT      NOT NULL                COMMENT '帖子ID，外键→post.post_id',
  `fav_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '收藏时间',
  PRIMARY KEY (`fav_id`),
  UNIQUE KEY `uk_user_post` (`user_id`, `post_id`),
  KEY `idx_fav_post` (`post_id`),
  CONSTRAINT `fk_fav_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_fav_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`post_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户收藏表（用户-帖子多对多中间表）';

-- 8. 帖子标签关联表（帖子 M:N 标签）
CREATE TABLE IF NOT EXISTS `post_tag` (
  `id`      INT NOT NULL AUTO_INCREMENT COMMENT '关联记录ID，主键',
  `post_id` INT NOT NULL                COMMENT '帖子ID，外键→post.post_id',
  `tag_id`  INT NOT NULL                COMMENT '标签ID，外键→tag.tag_id',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_post_tag` (`post_id`, `tag_id`),
  KEY `idx_pt_tag` (`tag_id`),
  CONSTRAINT `fk_pt_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`post_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_pt_tag`  FOREIGN KEY (`tag_id`)  REFERENCES `tag`  (`tag_id`)  ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='帖子-标签关联表（多对多中间表）';

-- 9. 点赞记录表：防止重复点赞，支持取消点赞
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

-- 10. 通知表：点赞/评论/置顶/加精/删帖时给帖主发通知
-- post_id 为可空：帖子删除时置空（ON DELETE SET NULL），通知本身保留
CREATE TABLE IF NOT EXISTS `notification` (
  `id`           INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id`      INT          NOT NULL COMMENT '接收通知的用户ID（帖主）',
  `type`         VARCHAR(20)  NOT NULL COMMENT '通知类型：like=点赞 / comment=评论 / top=置顶 / essence=加精 / delete=删帖',
  `post_id`      INT          DEFAULT NULL COMMENT '相关帖子ID（帖子删除后置空）',
  `from_user_id` INT          NOT NULL COMMENT '触发通知的用户ID',
  `content`      VARCHAR(255) DEFAULT NULL COMMENT '通知内容摘要',
  `is_read`      TINYINT      NOT NULL DEFAULT 0 COMMENT '是否已读：0=未读，1=已读',
  `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '通知时间',
  PRIMARY KEY (`id`),
  KEY `idx_user` (`user_id`),
  KEY `idx_unread` (`user_id`, `is_read`),
  CONSTRAINT `fk_notify_user`  FOREIGN KEY (`user_id`)      REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notify_from`  FOREIGN KEY (`from_user_id`) REFERENCES `user` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notify_post`  FOREIGN KEY (`post_id`)      REFERENCES `post` (`post_id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='系统通知表';
