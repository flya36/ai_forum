# ============================================================
# 管理员服务：用户管理 / 板块管理 / 内容管理
# ============================================================
from db import get_cursor
from notification_service import add_notification


# ========== 用户管理 ==========
def get_all_users():
    """获取全部用户列表（含封禁原因与到期时间）"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT user_id, username, nickname, role, status,
                   ban_reason, ban_until, create_time
            FROM `user` ORDER BY user_id
        """)
        rows = cursor.fetchall()
        return [{
            "user_id": r[0], "username": r[1], "nickname": r[2],
            "role": r[3], "status": r[4],
            "ban_reason": r[5], "ban_until": str(r[6]) if r[6] else None,
            "create_time": str(r[7]),
        } for r in rows]


def toggle_user_status(user_id):
    """切换用户状态：正常↔禁用，返回 (new_status, message)
    系统账号（AI 助手）拒绝禁用。"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT status, username FROM `user` WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                return None, "用户不存在"
            if row[1] == "ai_assistant":
                return None, "系统账号不可禁用"
            new_status = 1 if row[0] == 0 else 0
            cursor.execute("UPDATE `user` SET status = ? WHERE user_id = ?", (new_status, user_id))
        msg = "已禁用" if new_status == 1 else "已启用"
        return new_status, msg
    except Exception as e:
        print(f"[切换用户状态失败] {e}")
        return None, "操作失败，请稍后重试"


# ========== 板块管理 ==========
def add_board(name, description):
    """新增板块"""
    if not name.strip():
        return False, "板块名称不能为空"
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT board_id FROM board WHERE board_name = ?", (name,))
            if cursor.fetchone():
                return False, "板块名称已存在"
            cursor.execute(
                "INSERT INTO board (board_name, description) VALUES (?, ?)",
                (name.strip(), description.strip()),
            )
        return True, "添加成功"
    except Exception as e:
        print(f"[新增板块失败] {e}")
        return False, "添加失败，请稍后重试"


def update_board(board_id, name, description):
    """编辑板块"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE board SET board_name = ?, description = ? WHERE board_id = ?",
                (name.strip(), description.strip(), board_id),
            )
        return True, "修改成功"
    except Exception as e:
        print(f"[编辑板块失败] {e}")
        return False, "修改失败，请稍后重试"


def delete_board(board_id):
    """删除板块（级联删除该板块下所有帖子）"""
    try:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM board WHERE board_id = ?", (board_id,))
        # 级联删除的帖子可能仍在RAG索引缓存中，删除后使其失效
        try:
            from rag_service import invalidate_index
            invalidate_index()
        except Exception:
            pass
        return True, "删除成功"
    except Exception as e:
        print(f"[删除板块失败] {e}")
        return False, "删除失败，请稍后重试"


# ========== 内容管理 - 帖子 ==========
def get_all_posts(show_anon_author=False):
    """获取全部帖子（含作者、板块名、私密/匿名状态）。
    隐私约定（权限收紧）：
    - 私密帖：作者列恒为"保密"（管理员不获取私密帖作者信息，防侧信道）；
    - 匿名帖：对管理员默认掩码为"匿名用户"（show_anon_author=False）；
      确需核验真实作者时，由调用方显式开启并配套审计（见 app.py admin 路由审计钩子）。
    任何情况下都不返回作者 user_id。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, b.board_name,
                   p.view_count, p.like_count, p.is_top, p.is_essence,
                   p.is_private, p.is_anonymous, p.create_time
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            JOIN board b ON p.board_id = b.board_id
            ORDER BY p.create_time DESC
        """)
        rows = cursor.fetchall()
        return [{
            "post_id": r[0], "title": r[1],
            # 私密帖恒"保密"；匿名帖默认"匿名用户"（管理员同样遵守匿名承诺）
            "author": "保密" if r[8] else (r[2] if (not r[9] or show_anon_author) else "匿名用户"),
            "board_name": r[3],
            "view_count": r[4], "like_count": r[5],
            "is_top": r[6], "is_essence": r[7],
            "is_private": r[8], "is_anonymous": r[9],
            "create_time": str(r[10]),
        } for r in rows]


def get_post_for_review(post_id):
    """后台帖子审核预览（脱敏，权限收紧）：
    - 公开帖：正常显示作者昵称；
    - 私密帖/匿名帖：作者区域显示"保密"/"匿名用户"，正文可见（供审核）；
    - 返回结构不含 author_id / 真实昵称（私密/匿名时），防止在 HTML/JSON 中泄露。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT p.post_id, p.title, p.content, u.nickname, b.board_name,
                   p.view_count, p.like_count, p.is_top, p.is_essence,
                   p.is_private, p.is_anonymous, p.create_time
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            JOIN board b ON p.board_id = b.board_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if not row:
            return None
        is_private, is_anonymous = row[9], row[10]
        return {
            "post_id": row[0], "title": row[1], "content": row[2],
            # 作者掩码：私密帖"保密"，匿名帖"匿名用户"；公开帖显示真实昵称；
            # 绝不返回 author_id（前端无法据此构造封禁/查询请求）
            "author": "保密" if is_private else ("匿名用户" if is_anonymous else row[3]),
            "board_name": row[4],
            "view_count": row[5], "like_count": row[6],
            "is_top": row[7], "is_essence": row[8],
            "is_private": is_private, "is_anonymous": is_anonymous,
            "create_time": str(row[11]),
        }


def delete_post(post_id, operator_id=None, notify_content=None):
    """删除帖子（级联删除评论、收藏、点赞、分块、标签关联），并通知帖主。
    notify_content：自定义通知文案（如"你的帖子因违规被删除：xxx"），
    不透露举报人身份；默认"管理员删除了你的帖子"。"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT author_id, title FROM post WHERE post_id = ?", (post_id,))
            row = cursor.fetchone()
            if not row:
                return False, "帖子不存在"
            author_id, title = row[0], row[1]

            # 先发通知（帖子删除后 notification.post_id 会被 SET NULL，但内容已保留标题）
            if author_id and operator_id and operator_id != author_id:
                content = notify_content or f"管理员删除了你的帖子：{title}"
                add_notification(
                    user_id=author_id, notify_type="delete", post_id=post_id,
                    from_user_id=operator_id,
                    content=content,
                )

            cursor.execute("DELETE FROM post WHERE post_id = ?", (post_id,))
        # 使RAG索引缓存失效，下次检索时自动重建
        try:
            from rag_service import invalidate_index
            invalidate_index()
        except Exception:
            pass
        return True, "删除成功"
    except Exception as e:
        print(f"[管理员删帖失败] {e}")
        return False, "删除失败，请稍后重试"


def toggle_top(post_id, operator_id=None):
    """切换置顶状态，并通知帖主"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT is_top, author_id, title FROM post WHERE post_id = ?", (post_id,))
            row = cursor.fetchone()
            if not row:
                return None, "帖子不存在"
            old_val, author_id, title = row[0], row[1], row[2]
            new_val = 1 if old_val == 0 else 0
            cursor.execute("UPDATE post SET is_top = ? WHERE post_id = ?", (new_val, post_id))
        msg = "已置顶" if new_val == 1 else "已取消置顶"
        if operator_id and operator_id != author_id:
            add_notification(
                user_id=author_id, notify_type="top", post_id=post_id,
                from_user_id=operator_id,
                content=f"管理员将你的帖子《{title}》{msg}",
            )
        return new_val, msg
    except Exception as e:
        print(f"[置顶操作失败] {e}")
        return None, "操作失败，请稍后重试"


def toggle_essence(post_id, operator_id=None):
    """切换加精状态，并通知帖主"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT is_essence, author_id, title FROM post WHERE post_id = ?", (post_id,))
            row = cursor.fetchone()
            if not row:
                return None, "帖子不存在"
            old_val, author_id, title = row[0], row[1], row[2]
            new_val = 1 if old_val == 0 else 0
            cursor.execute("UPDATE post SET is_essence = ? WHERE post_id = ?", (new_val, post_id))
        msg = "已加精" if new_val == 1 else "已取消加精"
        if operator_id and operator_id != author_id:
            add_notification(
                user_id=author_id, notify_type="essence", post_id=post_id,
                from_user_id=operator_id,
                content=f"管理员将你的帖子《{title}》{msg}",
            )
        return new_val, msg
    except Exception as e:
        print(f"[加精操作失败] {e}")
        return None, "操作失败，请稍后重试"


# ========== 内容管理 - 评论 ==========
def get_all_comments():
    """获取全部评论（楼层用窗口函数按帖子动态计算）
    含 user_id/post_id：前端据此识别 AI 助手评论（撤回按钮）"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT c.comment_id, c.content, c.create_time, u.nickname, p.title,
                   ROW_NUMBER() OVER (PARTITION BY c.post_id ORDER BY c.create_time ASC, c.comment_id ASC) AS floor,
                   u.user_id, c.post_id
            FROM comment c
            JOIN `user` u ON c.user_id = u.user_id
            JOIN post p ON c.post_id = p.post_id
            ORDER BY c.create_time DESC
        """)
        rows = cursor.fetchall()
        return [{
            "comment_id": r[0], "content": r[1],
            "create_time": str(r[2]), "nickname": r[3], "post_title": r[4],
            "floor": r[5], "user_id": r[6], "post_id": r[7],
        } for r in rows]


def get_ai_assistant_id():
    """AI 助手系统账号 user_id（用于评论管理页识别可撤回评论）"""
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id FROM `user` WHERE username = 'ai_assistant'")
        row = cursor.fetchone()
        return row[0] if row else None


def revoke_ai_reply(comment_id):
    """撤回 AI 助手评论（永久跳过语义）：
    1. 删除该 AI 评论；
    2. 按 comment_id 精确删除对应通知（历史 NULL 数据按内容前缀兜底）；
    3. 将该帖 ai_replied 置为 1 —— 撤回视为"永久跳过"，后续扫描不再回复。
    仅允许撤回 ai_assistant 发表的评论。返回 (success, message, post_id)。"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT c.post_id, c.user_id, c.comment_id FROM comment c "
                "WHERE c.comment_id = ?",
                (comment_id,))
            row = cursor.fetchone()
            if not row:
                return False, "评论不存在", None
            post_id, uid, _ = row[0], row[1], row[2]
            cursor.execute(
                "SELECT username FROM `user` WHERE user_id = ?", (uid,))
            urow = cursor.fetchone()
            if not urow or urow[0] != "ai_assistant":
                return False, "仅可撤回 AI 助手发表的评论", None
            # 1. 删除评论
            cursor.execute("DELETE FROM comment WHERE comment_id = ?", (comment_id,))
            # 2. 删除通知：优先 comment_id 精确匹配；历史 NULL 用内容前缀兜底
            cursor.execute(
                "SELECT user_id FROM `user` WHERE username = 'ai_assistant'")
            ai_row = cursor.fetchone()
            if ai_row:
                ai_uid = ai_row[0]
                cursor.execute(
                    "DELETE FROM notification WHERE from_user_id = ? AND post_id = ? "
                    "AND type = 'comment' AND comment_id = ?",
                    (ai_uid, post_id, comment_id))
                if not cursor.rowcount:
                    # 兜底：旧数据 comment_id=NULL
                    cursor.execute(
                        "DELETE FROM notification WHERE from_user_id = ? AND post_id = ? "
                        "AND type = 'comment' AND comment_id IS NULL "
                        "AND content LIKE 'AI 助手 评论了你的帖子%'",
                        (ai_uid, post_id))
            # 3. 永久跳过：ai_replied=1（撤回后不再触发 AI 回复）
            cursor.execute(
                "UPDATE post SET ai_replied = 1 WHERE post_id = ?", (post_id,))
        return True, "已撤回 AI 回复（该帖不再自动回复）", post_id
    except Exception as e:
        print(f"[撤回 AI 回复失败] {e}")
        return False, "撤回失败，请稍后重试", None


def delete_comment(comment_id):
    """删除评论（楼层由查询动态计算，无需重新编号）
    级联清理：同步删除该评论的全部点赞记录（comment_like）。
    外键已配置 ON DELETE CASCADE 兜底，这里显式删除双保险。"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT comment_id FROM comment WHERE comment_id = ?", (comment_id,))
            if not cursor.fetchone():
                return False, "评论不存在"
            cursor.execute("DELETE FROM comment_like WHERE comment_id = ?", (comment_id,))
            cursor.execute("DELETE FROM comment WHERE comment_id = ?", (comment_id,))
        return True, "删除成功"
    except Exception as e:
        print(f"[删除评论失败] {e}")
        return False, "删除失败，请稍后重试"
