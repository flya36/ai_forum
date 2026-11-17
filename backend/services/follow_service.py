# ============================================================
# 关注服务：关注 / 取关 / 计数 / 关注与粉丝列表（分页）
# 数据表 user_follows：单向关注，唯一索引(follower_id, followee_id) 防重复
# 语义：follow_user 幂等关注（已关注返回 False"已关注"，不重复插入）；
#       unfollow_user 幂等取关。前后端按按钮状态决定调用哪个动作。
# 安全约定：
#   - 不允许关注自己（Service 层强制）
#   - 所有 SQL 参数化
#   - 越权由路由层校验 session（见 app.py follow 路由）
# ============================================================
from db import get_cursor


def follow_user(follower_id, followee_id):
    """幂等关注。返回 (ok, msg)。已关注时返回 False（不重复插入，唯一索引兜底）。"""
    if follower_id == followee_id:
        return False, "不能关注自己"
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id FROM `user` WHERE user_id = ?", (followee_id,))
        if not cursor.fetchone():
            return False, "用户不存在"
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id))
        if cursor.fetchone()[0] > 0:
            return False, "已关注"
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_follows (follower_id, followee_id) VALUES (?, ?)",
                (follower_id, followee_id))
        return True, "关注成功"
    except Exception as e:
        print(f"[关注失败] {e}")
        return False, "已关注"


def unfollow_user(follower_id, followee_id):
    """幂等取关。返回 (ok, msg)。"""
    if follower_id == followee_id:
        return False, "不能操作自己"
    with get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id))
    return True, "已取消关注"


def is_following(follower_id, followee_id):
    """follower_id 是否已关注 followee_id"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id))
        return cursor.fetchone()[0] > 0


def get_follow_counts(user_id):
    """返回 (关注数, 粉丝数)"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ?", (user_id,))
        following = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE followee_id = ?", (user_id,))
        followers = cursor.fetchone()[0]
    return following, followers


def get_following_list(user_id, page=1, page_size=20):
    """user_id 关注的用户列表（分页），返回 (total, users)"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ?", (user_id,))
        total = cursor.fetchone()[0]
        cursor.execute("""
            SELECT u.user_id, u.username, u.nickname, u.signature, u.avatar_path, u.role
            FROM user_follows f
            JOIN `user` u ON f.followee_id = u.user_id
            WHERE f.follower_id = ?
            ORDER BY f.id DESC
            LIMIT ? OFFSET ?
        """, (user_id, page_size, (page - 1) * page_size))
        rows = cursor.fetchall()
    users = [{
        "user_id": r[0], "username": r[1], "nickname": r[2] or "",
        "signature": r[3] or "", "avatar_path": r[4], "role": r[5],
    } for r in rows]
    return total, users


def get_followers_list(user_id, page=1, page_size=20):
    """关注 user_id 的用户列表（粉丝，分页），返回 (total, users)"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE followee_id = ?", (user_id,))
        total = cursor.fetchone()[0]
        cursor.execute("""
            SELECT u.user_id, u.username, u.nickname, u.signature, u.avatar_path, u.role
            FROM user_follows f
            JOIN `user` u ON f.follower_id = u.user_id
            WHERE f.followee_id = ?
            ORDER BY f.id DESC
            LIMIT ? OFFSET ?
        """, (user_id, page_size, (page - 1) * page_size))
        rows = cursor.fetchall()
    users = [{
        "user_id": r[0], "username": r[1], "nickname": r[2] or "",
        "signature": r[3] or "", "avatar_path": r[4], "role": r[5],
    } for r in rows]
    return total, users


# ============================================================
# 好友列表：好友 = 互相关注（A 关注 B 且 B 关注 A）
# ============================================================
def get_friends_list(user_id, page=1, page_size=20):
    """user_id 的好友列表（互相关注，分页），返回 (total, users)。
    关注时间取双向关注建立中较晚者。SQL 全参数化。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM user_follows a
            JOIN user_follows b
              ON a.followee_id = b.follower_id AND b.followee_id = a.follower_id
            WHERE a.follower_id = ? AND a.followee_id != ?
        """, (user_id, user_id))
        total = cursor.fetchone()[0]
        cursor.execute("""
            SELECT u.user_id, u.username, u.nickname, u.signature, u.avatar_path, u.role,
                   CASE WHEN a.created_at >= b.created_at THEN a.created_at ELSE b.created_at END AS friend_at
            FROM user_follows a
            JOIN user_follows b
              ON a.followee_id = b.follower_id AND b.followee_id = a.follower_id
            JOIN `user` u ON a.followee_id = u.user_id
            WHERE a.follower_id = ? AND a.followee_id != ?
            ORDER BY friend_at DESC
            LIMIT ? OFFSET ?
        """, (user_id, user_id, page_size, (page - 1) * page_size))
        rows = cursor.fetchall()
    users = [{
        "user_id": r[0], "username": r[1], "nickname": r[2] or "",
        "signature": r[3] or "", "avatar_path": r[4], "role": r[5],
        "friend_at": str(r[6])[:16] if r[6] else "",
    } for r in rows]
    return total, users


# ============================================================
# 好友判定：好友 = 互相关注（A 关注 B 且 B 关注 A）
# ============================================================
def is_friend(a_id, b_id):
    """互相关注即为好友。参数化 SQL。"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (a_id, b_id))
        ab = cursor.fetchone()[0] > 0
        cursor.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (b_id, a_id))
        ba = cursor.fetchone()[0] > 0
        return ab and ba


# ============================================================
# 拉黑：user_blocks（blocker_id, blocked_id 唯一）
# ============================================================
def is_blocked(blocker_id, blocked_id):
    """blocker 是否拉黑了 blocked。"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id))
        return cursor.fetchone()[0] > 0


def block_user(blocker_id, blocked_id):
    """拉黑：插入记录 + 删除双方之间所有关注关系（双向）。
    返回 (ok, msg)。"""
    if blocker_id == blocked_id:
        return False, "不能拉黑自己"
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id FROM `user` WHERE user_id = ?", (blocked_id,))
        if not cursor.fetchone():
            return False, "用户不存在"
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM user_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id))
        if cursor.fetchone()[0] > 0:
            return False, "已拉黑"
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_blocks (blocker_id, blocked_id) VALUES (?, ?)",
                (blocker_id, blocked_id))
            # 删除双方之间的所有关注关系（双向）
            cursor.execute(
                "DELETE FROM user_follows WHERE (follower_id = ? AND followee_id = ?)"
                " OR (follower_id = ? AND followee_id = ?)",
                (blocker_id, blocked_id, blocked_id, blocker_id))
        return True, "已拉黑"
    except Exception as e:
        print("[拉黑失败]", e)
        return False, "拉黑失败"


def unblock_user(blocker_id, blocked_id):
    """解除拉黑。返回 (ok, msg)。"""
    if blocker_id == blocked_id:
        return False, "不能操作自己"
    with get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM user_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id))
    return True, "已解除拉黑"


def get_block_list(user_id):
    """当前用户已拉黑用户列表（最新在前）。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT u.user_id, u.username, u.nickname, u.avatar_path, b.created_at
            FROM user_blocks b
            JOIN `user` u ON b.blocked_id = u.user_id
            WHERE b.blocker_id = ?
            ORDER BY b.created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
    return [{
        "user_id": r[0], "username": r[1], "nickname": r[2] or "",
        "avatar_path": r[3],
        "created_at": str(r[4])[:16] if r[4] else "",
    } for r in rows]
