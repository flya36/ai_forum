# -*- coding: utf-8 -*-
"""清理 flya36(2) 和 fqq(3) 之间的关注与私信，为录制演示准备干净状态。
保留：不删账号、不删帖子、不删通知。只删两人之间的直接关注和私信。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'core'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'config'))
from db import get_cursor

A, B = 2, 3  # flya36, fqq

with get_cursor() as c:
    # 查现状
    c.execute("SELECT * FROM user_follows WHERE (follower_id=? AND followee_id=?) OR (follower_id=? AND followee_id=?)", (A,B,B,A))
    print('当前关注关系:', c.fetchall())
    c.execute("SELECT * FROM message WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)", (A,B,B,A))
    rows = c.fetchall()
    print('当前私信条数:', len(rows))

    # 删除两人之间的关注（双向）
    c.execute("DELETE FROM user_follows WHERE (follower_id=? AND followee_id=?) OR (follower_id=? AND followee_id=?)", (A,B,B,A))
    print('删除关注数:', c.rowcount)

    # 删除两人之间的私信
    c.execute("DELETE FROM message WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)", (A,B,B,A))
    print('删除私信数:', c.rowcount)

    # 只保留 A 单向关注 B（演示"单向关注破冰"）
    c.execute("INSERT INTO user_follows (follower_id, followee_id) VALUES (?, ?)", (A, B))
    print('已建立 A(%d) 单向关注 B(%d)' % (A, B))

print('完成。现在状态：A 关注 B，B 不关注 A，无历史私信。')
