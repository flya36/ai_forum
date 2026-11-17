# ============================================================
# 用户服务：个人信息查询 / 修改 / 统计 / 发帖列表
# ============================================================
from db import get_cursor


def get_user_detail(user_id, viewer_id=None):
    """获取用户完整信息 + 统计数据；看别人时发帖数只统计公开+本名帖"""
    with get_cursor() as cursor:
        # 看自己时统计全部发帖，看别人时只统计公开+本名帖
        if viewer_id and viewer_id == user_id:
            post_count_sql = "(SELECT COUNT(*) FROM post WHERE author_id = u.user_id)"
            likes_sql = "(SELECT COALESCE(SUM(like_count), 0) FROM post WHERE author_id = u.user_id)"
        else:
            post_count_sql = "(SELECT COUNT(*) FROM post WHERE author_id = u.user_id AND is_private = 0 AND is_anonymous = 0)"
            likes_sql = "(SELECT COALESCE(SUM(like_count), 0) FROM post WHERE author_id = u.user_id AND is_private = 0 AND is_anonymous = 0)"

        cursor.execute(f"""
            SELECT u.user_id, u.username, u.nickname, u.signature, u.avatar_path,
                   u.role, u.status, u.create_time,
                   {post_count_sql} AS post_count,
                   (SELECT COUNT(*) FROM comment WHERE user_id = u.user_id) AS comment_count,
                   {likes_sql} AS total_likes
            FROM `user` u
            WHERE u.user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0], "username": row[1], "nickname": row[2] or "",
            "signature": row[3] or "", "avatar_path": row[4],
            "role": row[5], "status": row[6],
            "create_time": str(row[7]),
            "post_count": row[8], "comment_count": row[9], "total_likes": row[10],
        }


def update_profile(user_id, nickname, signature):
    """修改昵称和个性签名"""
    if not nickname.strip():
        return False, "昵称不能为空"
    # 敏感词过滤（参数化 SQL 之前；命中拒绝并审计）
    from word_filter import block_text
    blocked, msg = block_text(
        "%s\n%s" % (nickname, signature or ""), user_id, "profile", user_id)
    if blocked:
        return False, msg
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE `user` SET nickname = ?, signature = ? WHERE user_id = ?",
                (nickname.strip(), signature.strip(), user_id),
            )
        return True, "修改成功"
    except Exception as e:
        print(f"[更新资料失败] {e}")
        return False, "修改失败，请稍后重试"


def update_avatar(user_id, avatar_filename):
    """更新头像文件名（文件已存到 avatars/ 目录），传 None 表示清除头像"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE `user` SET avatar_path = ? WHERE user_id = ?",
                (avatar_filename, user_id),
            )
        return True, "头像更新成功"
    except Exception as e:
        print(f"[更新头像失败] {e}")
        return False, "头像更新失败，请稍后重试"


def get_user_posts(user_id, viewer_id=None, limit=50):
    """获取用户发布的帖子列表；viewer_id 为查看者ID，看自己时含私密帖子"""
    with get_cursor() as cursor:
        if viewer_id and viewer_id == user_id:
            # 看自己的帖子：全部（含私密）
            cursor.execute("""
                SELECT p.post_id, p.title, b.board_name, p.view_count,
                       p.like_count, p.create_time, p.is_private, p.is_anonymous,
                       (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count
                FROM post p
                JOIN board b ON p.board_id = b.board_id
                WHERE p.author_id = ?
                ORDER BY p.create_time DESC
                LIMIT ?
            """, (user_id, limit))
        else:
            # 看别人的帖子：仅公开+本名
            cursor.execute("""
                SELECT p.post_id, p.title, b.board_name, p.view_count,
                       p.like_count, p.create_time, p.is_private, p.is_anonymous,
                       (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count
                FROM post p
                JOIN board b ON p.board_id = b.board_id
                WHERE p.author_id = ? AND p.is_private = 0 AND p.is_anonymous = 0
                ORDER BY p.create_time DESC
                LIMIT ?
            """, (user_id, limit))
        rows = cursor.fetchall()
        return [{
            "post_id": r[0], "title": r[1], "board_name": r[2],
            "view_count": r[3], "like_count": r[4],
            "create_time": str(r[5]), "is_private": r[6], "is_anonymous": r[7],
            "reply_count": r[8],
        } for r in rows]


def delete_account(user_id):
    """注销账号：删除用户及其所有关联数据（级联），返回 (success, message)"""
    try:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM `user` WHERE user_id = ?", (user_id,))
        # 使RAG索引缓存失效
        try:
            from rag_service import invalidate_index
            invalidate_index()
        except Exception:
            pass
        return True, "账号已注销"
    except Exception as e:
        print(f"[注销账号失败] {e}")
        return False, "注销失败，请稍后重试"
