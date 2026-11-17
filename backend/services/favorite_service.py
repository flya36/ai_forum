# ============================================================
# 收藏服务：收藏 / 取消收藏 / 判断是否已收藏
# ============================================================
from db import get_cursor


def is_favorited(user_id, post_id):
    """判断用户是否已收藏该帖子"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT fav_id FROM favorite WHERE user_id = ? AND post_id = ?",
            (user_id, post_id),
        )
        return cursor.fetchone() is not None


def toggle_favorite(user_id, post_id):
    """切换收藏状态：已收藏则取消，未收藏则添加。
    返回 (is_favorited: bool, favorite_count: int, message: str)"""
    # 封禁检查（Service 层）：被封禁用户不可收藏
    from ban_service import is_user_banned
    if is_user_banned(user_id)[0]:
        return False, 0, "账号已被封禁，无法执行此操作"
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT fav_id FROM favorite WHERE user_id = ? AND post_id = ?",
                (user_id, post_id),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute("DELETE FROM favorite WHERE fav_id = ?", (row[0],))
                favorited = False
                msg = "已取消收藏"
            else:
                cursor.execute(
                    "INSERT INTO favorite (user_id, post_id) VALUES (?, ?)",
                    (user_id, post_id),
                )
                favorited = True
                msg = "收藏成功"
            cursor.execute("SELECT COUNT(*) FROM favorite WHERE post_id = ?", (post_id,))
            count = cursor.fetchone()[0]
        return favorited, count, msg
    except Exception as e:
        print(f"[收藏操作失败] {e}")
        return False, 0, "操作失败，请稍后重试"


def get_favorites_by_user(user_id, viewer_id=None):
    """获取用户的收藏列表；字段对齐首页帖子卡片（作者头像/摘要/关注与拉黑状态）。
    匿名帖不暴露真实作者身份；viewer_id 为查看者（即收藏者本人），用于关注/拉黑状态判断。"""
    vid = viewer_id or user_id
    from post_service import _md_to_text
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                   p.is_top, p.is_essence, p.is_anonymous, p.author_id,
                   u.avatar_path, p.content, p.like_count, p.is_private,
                   (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count,
                   EXISTS(SELECT 1 FROM user_follows f2
                          WHERE f2.follower_id = ? AND f2.followee_id = p.author_id) AS is_following,
                   EXISTS(SELECT 1 FROM user_blocks b
                          WHERE (b.blocker_id = ? AND b.blocked_id = p.author_id)
                             OR (b.blocker_id = p.author_id AND b.blocked_id = ?)) AS is_blocked
            FROM favorite f
            JOIN post p ON f.post_id = p.post_id
            JOIN `user` u ON p.author_id = u.user_id
            WHERE f.user_id = ?
            ORDER BY f.fav_time DESC
        """, (vid, vid, vid, user_id))
        rows = cursor.fetchall()
        out = []
        for r in rows:
            is_anon = bool(r[7])
            out.append({
                "post_id": r[0], "title": r[1],
                # 匿名帖不暴露真实作者昵称
                "author": "匿名用户" if is_anon else r[2],
                "author_id": None if is_anon else r[8],
                "author_avatar": None if is_anon else r[9],
                "summary": _md_to_text(r[10]),
                "create_time": str(r[3]), "view_count": r[4],
                "is_top": r[5], "is_essence": r[6], "is_anonymous": is_anon,
                "is_private": bool(r[12]),
                "reply_count": r[13], "like_count": r[11],
                "is_following": False if is_anon else bool(r[14]),
                "is_blocked": False if is_anon else bool(r[15]),
            })
        return out
