# ============================================================
# 评论服务：评论的查询与发表
# 楼层号由查询时用 ROW_NUMBER() 动态计算，避免并发写入时的重复楼层
# ============================================================
from db import get_cursor


def get_comments_by_post(post_id, viewer_id=None):
    """获取某帖子的全部评论，楼层号动态计算，按时间升序。
    viewer_id：当前查看者，用于返回该用户对每条评论的已赞状态（comment_like）。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT c.comment_id, c.content, c.create_time, u.nickname, u.user_id,
                   u.avatar_path,
                   ROW_NUMBER() OVER (ORDER BY c.create_time ASC, c.comment_id ASC) AS floor,
                   (SELECT COUNT(*) FROM comment_like cl WHERE cl.comment_id = c.comment_id) AS like_count,
                   EXISTS(SELECT 1 FROM comment_like cl2
                          WHERE cl2.comment_id = c.comment_id AND cl2.user_id = ?) AS liked
            FROM comment c
            JOIN `user` u ON c.user_id = u.user_id
            WHERE c.post_id = ?
            ORDER BY c.create_time ASC, c.comment_id ASC
        """, (viewer_id or 0, post_id))
        rows = cursor.fetchall()
        return [{
            "comment_id": r[0], "content": r[1],
            "create_time": str(r[2]), "nickname": r[3], "user_id": r[4],
            "avatar_path": r[5],
            "floor": r[6],
            "like_count": r[7], "liked": bool(r[8]),
        } for r in rows]


def add_comment(post_id, user_id, content):
    """发表评论，返回 (success, message, comment_id)；comment_id 用于 @提及 跳转锚点"""
    if not content.strip():
        return False, "评论内容不能为空", None
    # 封禁检查（Service 层，不依赖前端/路由）：被封禁用户不可发表评论
    from ban_service import is_user_banned
    if is_user_banned(user_id)[0]:
        return False, "账号已被封禁，无法执行此操作", None
    # 敏感词过滤（参数化 SQL 之前；命中拒绝并审计）
    from word_filter import block_text
    blocked, msg = block_text(content, user_id, "comment", post_id)
    if blocked:
        return False, msg, None

    try:
        with get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO comment (post_id, user_id, content)
                VALUES (?, ?, ?)
            """, (post_id, user_id, content.strip()))
            # 取新评论 ID（lastrowid 优先，回退按最新一条查询），供 @提及 通知跳转锚点
            new_id = getattr(cursor, "lastrowid", None)
            if not new_id:
                cursor.execute(
                    "SELECT comment_id FROM comment WHERE post_id = ? AND user_id = ? ORDER BY comment_id DESC LIMIT 1",
                    (post_id, user_id),
                )
                row = cursor.fetchone()
                new_id = row[0] if row else None
        return True, "评论成功", new_id
    except Exception as e:
        print(f"[评论失败] {e}")
        return False, "评论失败，请稍后重试", None
