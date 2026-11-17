# ============================================================
# 点赞服务：防重复点赞 / 取消点赞 / 查询是否已点赞
# ============================================================
from db import get_cursor


def is_liked(user_id, post_id):
    """判断用户是否已点赞该帖子"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT id FROM post_like WHERE user_id = ? AND post_id = ?",
            (user_id, post_id),
        )
        return cursor.fetchone() is not None


def toggle_like(user_id, post_id):
    """
    切换点赞状态：未点赞→点赞(+1)，已点赞→取消(-1)
    返回 (is_liked: bool, like_count: int, message: str)
    """
    # 封禁检查（Service 层）：被封禁用户不可点赞
    from ban_service import is_user_banned
    if is_user_banned(user_id)[0]:
        return False, 0, "账号已被封禁，无法执行此操作"
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM post_like WHERE user_id = ? AND post_id = ?",
                (user_id, post_id),
            )
            row = cursor.fetchone()

            if row:
                # 已点赞 → 取消
                cursor.execute("DELETE FROM post_like WHERE id = ?", (row[0],))
                cursor.execute("UPDATE post SET like_count = like_count - 1 WHERE post_id = ?", (post_id,))
                liked = False
                msg = "已取消点赞"
            else:
                # 未点赞 → 点赞
                cursor.execute(
                    "INSERT INTO post_like (user_id, post_id) VALUES (?, ?)",
                    (user_id, post_id),
                )
                cursor.execute("UPDATE post SET like_count = like_count + 1 WHERE post_id = ?", (post_id,))
                liked = True
                msg = "点赞成功"

            cursor.execute("SELECT like_count FROM post WHERE post_id = ?", (post_id,))
            count = cursor.fetchone()[0]
        return liked, count, msg
    except Exception as e:
        print(f"[点赞操作失败] {e}")
        return False, 0, "操作失败，请稍后重试"
