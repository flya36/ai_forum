# ============================================================
# 通知服务：发送通知 / 查询未读数 / 获取通知列表 / 标记已读
# 通知类型：like=点赞 / comment=评论 / top=置顶 / essence=加精 / delete=删除
# ============================================================
from db import get_cursor


def add_notification(user_id, notify_type, post_id, from_user_id, content="", comment_id=None):
    """
    给用户发一条通知。如果触发者就是帖主本人，不发通知。
    post_id 可为 None（如帖子已删除，仅保留文字内容）。
    comment_id 可为 None（评论场景传入，前端可跳转 #comment-锚点）。
    """
    if user_id == from_user_id:
        return  # 自己的操作不提醒自己

    try:
        with get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO notification (user_id, type, post_id, comment_id, from_user_id, content)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, notify_type, post_id, comment_id, from_user_id, content))
    except Exception as e:
        print(f"通知发送失败：{e}")


def get_unread_count(user_id):
    """获取用户未读通知数量（系统通知口径：排除私信 message / 分享 share，
    两者在私信 Tab 独立展示与计数，避免重复计入系统红点）"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM notification WHERE user_id = ? AND is_read = 0 "
            "AND type NOT IN ('message', 'share')",
            (user_id,),
        )
        return cursor.fetchone()[0]


def get_notifications(user_id, limit=50):
    """获取用户通知列表（最新在前）；帖子已删除时 post_title 为空。
    返回含 post_id / comment_id，供前端渲染跳转链接。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT n.id, n.type, n.content, n.is_read, n.create_time,
                   n.post_id, n.comment_id, u.nickname, p.title, n.from_user_id
            FROM notification n
            LEFT JOIN `user` u ON n.from_user_id = u.user_id
            LEFT JOIN post p ON n.post_id = p.post_id
            WHERE n.user_id = ? AND n.type NOT IN ('message', 'share')
            ORDER BY n.create_time DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "type": r[1], "content": r[2],
            "is_read": r[3], "create_time": str(r[4]),
            "post_id": r[5], "comment_id": r[6],
            "from_nickname": r[7], "post_title": r[8], "from_user_id": r[9],
        } for r in rows]


def mark_all_read(user_id):
    """将用户系统通知标记为已读（排除 message/share：私信已读由 message 表管理）"""
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE notification SET is_read = 1 WHERE user_id = ? AND is_read = 0 "
            "AND type NOT IN ('message', 'share')",
            (user_id,),
        )
