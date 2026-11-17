-- 表名单数统一：messages → message（无外键，索引随表保留；InnoDB RENAME 低成本）
RENAME TABLE messages TO message;
