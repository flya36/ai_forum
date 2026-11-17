# -*- coding: utf-8 -*-
# ============================================================
# 评论点赞服务：防重复点赞 / 取消点赞 / 查询计数与已赞状态
# 与帖子点赞（like_service）保持一致的语义，全部参数化 SQL
# ============================================================
from db import get_cursor


def is_liked(user_id, comment_id):
    """判断用户是否已点赞该评论"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT id FROM comment_like WHERE user_id = ? AND comment_id = ?",
            (user_id, comment_id),
        )
        return cursor.fetchone() is not None


def get_like_count(comment_id):
    """获取评论点赞数"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM comment_like WHERE comment_id = ?",
            (comment_id,),
        )
        return cursor.fetchone()[0]


def _get_comment(comment_id):
    """取评论行（comment_id, user_id, post_id）"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT comment_id, user_id, post_id FROM comment WHERE comment_id = ?",
            (comment_id,),
        )
        return cursor.fetchone()


def toggle_like(user_id, comment_id):
    """
    切换评论点赞状态：未点赞→点赞(+1)，已点赞→取消(-1)
    返回 (liked: bool, like_count: int, message: str)
    语义错误（评论不存在 / 自己赞自己 / 拉黑）通过 message 区分，由路由映射状态码：
      - 评论不存在          -> 404
      - 不能给自己的评论点赞 -> 400
      - 拉黑限制            -> 403
    """
    # 封禁检查（Service 层）：被封禁用户不可点赞
    from ban_service import is_user_banned
    if is_user_banned(user_id)[0]:
        return False, 0, "账号已被封禁，无法执行此操作"

    comment = _get_comment(comment_id)
    if not comment:
        return False, 0, "评论不存在"

    author_id = comment[1]
    if author_id == user_id:
        return False, 0, "不能给自己的评论点赞"

    # 拉黑检查（双向）：任一方向存在拉黑关系即拒绝
    # 需求场景：A 被 B 拉黑，A 不能给 B 的评论点赞；同时 A 拉黑了 B 也不应点赞其评论
    from follow_service import is_blocked
    if is_blocked(author_id, user_id) or is_blocked(user_id, author_id):
        return False, 0, "无法点赞该用户的评论"

    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM comment_like WHERE user_id = ? AND comment_id = ?",
                (user_id, comment_id),
            )
            row = cursor.fetchone()

            if row:
                cursor.execute("DELETE FROM comment_like WHERE id = ?", (row[0],))
                liked = False
                msg = "已取消点赞"
            else:
                cursor.execute(
                    "INSERT INTO comment_like (user_id, comment_id) VALUES (?, ?)",
                    (user_id, comment_id),
                )
                liked = True
                msg = "点赞成功"

            cursor.execute(
                "SELECT COUNT(*) FROM comment_like WHERE comment_id = ?",
                (comment_id,),
            )
            count = cursor.fetchone()[0]
        return liked, count, msg
    except Exception as e:
        print(f"[评论点赞操作失败] {e}")
        return False, 0, "操作失败，请稍后重试"


def delete_likes_by_comment(comment_id):
    """删除某评论的全部点赞记录（级联清理：管理员删除评论时调用）"""
    try:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM comment_like WHERE comment_id = ?", (comment_id,))
        return True
    except Exception as e:
        print(f"[评论点赞级联清理失败] {e}")
        return False
