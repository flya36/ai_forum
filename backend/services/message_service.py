# -*- coding: utf-8 -*-
# ============================================================
# 站内私信服务：发送 / 对话线程 / 收件箱 / 发件箱 / 未读数
# 安全：全部参数化 SQL；对话线程仅允许双方访问（路由层校验）；
#       content 入库存原文，渲染时走 Markdown + bleach 白名单（XSS）
# ============================================================
from db import get_cursor

def check_message_permission(sender_id, receiver_id):
    """私信权限收紧（后端强制）：
    1. 拉黑检查（双向）-> 拒绝
    2. 好友（互相关注）-> 允许无限发送
    3. 对话已建立（对方曾给我发过 >=1 条消息）-> 允许，无需关注（B 回复 A、A 续发均适用）
    4. 单向关注 -> 已发过但对方从未回复（等回复）拒绝；否则允许发 1 条
    5. 无关注、无历史对话 -> 拒绝
    返回 (ok, msg)。"""
    from follow_service import is_friend, is_blocked, is_following
    # 1. 拉黑检查（双向）
    if is_blocked(sender_id, receiver_id):
        return False, "对方已屏蔽你"
    if is_blocked(receiver_id, sender_id):
        return False, "你已屏蔽对方"
    # 2. 好友 -> 无限发送
    if is_friend(sender_id, receiver_id):
        return True, ""
    # 3. 对话已建立：对方曾给我发过消息 -> 允许（无需关注）
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT MAX(created_at) FROM message WHERE sender_id = ? AND receiver_id = ?",
            (receiver_id, sender_id))
        b2a = cursor.fetchone()[0]
    if b2a:
        return True, ""
    # 4. 单向关注
    if is_following(sender_id, receiver_id):
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT MAX(created_at) FROM message WHERE sender_id = ? AND receiver_id = ?",
                (sender_id, receiver_id))
            a2b = cursor.fetchone()[0]
        if a2b:
            return False, "对方尚未回复，请等待回复后再发送"
        return True, ""
    # 5. 无关注关系、无历史对话
    return False, "请先关注对方，或等待对方关注你"


def send_message(sender_id, receiver_id, content, post_id=None):
    """发送私信，返回消息 id；失败抛异常。post_id 非空表示帖子分享。"""
    with get_cursor() as cursor:
        if post_id is not None:
            cursor.execute(
                "INSERT INTO message (sender_id, receiver_id, content, post_id) VALUES (?, ?, ?, ?)",
                (sender_id, receiver_id, content, post_id))
        else:
            cursor.execute(
                "INSERT INTO message (sender_id, receiver_id, content) VALUES (?, ?, ?)",
                (sender_id, receiver_id, content))
        new_id = getattr(cursor, "lastrowid", None)
        if not new_id:
            cursor.execute(
                "SELECT MAX(id) FROM message WHERE sender_id = ? AND receiver_id = ?",
                (sender_id, receiver_id))
            row = cursor.fetchone()
            new_id = row[0] if row else None
        return new_id


def get_thread(user_id, other_id, limit=200):
    """与另一用户的完整对话线程（正序）。仅调用方保证双方身份。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_read, m.created_at, m.post_id
            FROM message m
            WHERE (m.sender_id = ? AND m.receiver_id = ?)
               OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.created_at ASC, m.id ASC
            LIMIT ?
        """, (user_id, other_id, other_id, user_id, limit))
        return [{
            "id": r[0], "sender_id": r[1], "receiver_id": r[2],
            "content": r[3], "is_read": r[4],
            "created_at": str(r[5])[:19] if r[5] else "",
            "post_id": r[6],
        } for r in cursor.fetchall()]


def mark_thread_read(user_id, other_id):
    """把与对方的未读消息标记为已读（只标记发给我的）。"""
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE message SET is_read = 1 WHERE receiver_id = ? AND sender_id = ? AND is_read = 0",
            (user_id, other_id))


def unread_message_count(user_id):
    """未读私信总数（导航红点）。"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM message WHERE receiver_id = ? AND is_read = 0",
            (user_id,))
        return cursor.fetchone()[0]


def get_inbox(user_id):
    """收件箱：按发件人聚合，含最新消息、未读数、最近时间。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT m.sender_id, u.nickname, u.username, u.status, u.avatar_path,
                   m.content, m.created_at, m.post_id,
                   (SELECT COUNT(*) FROM message x
                    WHERE x.receiver_id = ? AND x.sender_id = m.sender_id AND x.is_read = 0) AS unread
            FROM message m
            JOIN `user` u ON m.sender_id = u.user_id
            WHERE m.receiver_id = ?
              AND m.id = (SELECT MAX(y.id) FROM message y
                          WHERE y.receiver_id = m.receiver_id AND y.sender_id = m.sender_id)
            ORDER BY m.created_at DESC
        """, (user_id, user_id))
        return [{
            "other_id": r[0], "nickname": r[1], "username": r[2],
            "status": r[3], "avatar_path": r[4], "content": r[5],
            "created_at": str(r[6])[:16] if r[6] else "",
            "post_id": r[7], "unread": r[8],
        } for r in cursor.fetchall()]


def get_sent(user_id):
    """发件箱：按收件人聚合。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT m.receiver_id, u.nickname, u.username, u.avatar_path,
                   m.content, m.created_at, m.post_id
            FROM message m
            JOIN `user` u ON m.receiver_id = u.user_id
            WHERE m.sender_id = ?
              AND m.id = (SELECT MAX(y.id) FROM message y
                          WHERE y.sender_id = m.sender_id AND y.receiver_id = m.receiver_id)
            ORDER BY m.created_at DESC
        """, (user_id,))
        return [{
            "other_id": r[0], "nickname": r[1], "username": r[2],
            "avatar_path": r[3], "content": r[4],
            "created_at": str(r[5])[:16] if r[5] else "",
            "post_id": r[6],
        } for r in cursor.fetchall()]


def get_conversation_partners(user_id):
    """对话列表（收+发合并去重，按最近时间倒序）。"""
    merged = {}
    for row in get_inbox(user_id):
        merged[row["other_id"]] = row
    for row in get_sent(user_id):
        oid = row["other_id"]
        if oid in merged:
            if row["created_at"] > merged[oid]["created_at"]:
                merged[oid]["content"] = row["content"]
                merged[oid]["created_at"] = row["created_at"]
        else:
            row["unread"] = 0
            merged[oid] = row
    return sorted(merged.values(), key=lambda x: x["created_at"], reverse=True)
