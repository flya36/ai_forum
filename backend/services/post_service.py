# ============================================================
# 帖子服务：帖子的数据库查询与创建操作
# 支持私密/公开、匿名/本名
# ============================================================
import re

from db import get_cursor


def _mask_anonymous(author, is_anonymous):
    """匿名帖子作者显示为'匿名用户'"""
    return "匿名用户" if is_anonymous else author


def _md_to_text(content, limit=160):
    """Markdown 正文 → 纯文本摘要（去图片/链接/标记符号，压缩空白并截断）。
    仅用于列表页摘要展示，不参与渲染，无 XSS 风险。"""
    if not content:
        return ""
    s = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', content)  # 图片
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)      # 链接保留文字
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.M)        # 标题符号
    s = re.sub(r'[`>*]', '', s)                          # 行内代码/引用/列表符
    s = re.sub(r'(\*\*|__|\*|_|~~)', '', s)              # 强调/删除线
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:limit]


def _post_row(r, anon_idx, author_idx, avatar_idx, content_idx,
              reply_idx=None, like_idx=None):
    """将公共列表查询行转 dict（匿名帖不暴露作者身份与关注信息）。
    r: 查询行; anon_idx: is_anonymous 列下标; author_idx: author_id 列下标;
    avatar_idx: avatar_path 列下标; content_idx: content 列下标。"""
    is_anon = bool(r[anon_idx])
    return {
        "post_id": r[0], "title": r[1],
        "author": _mask_anonymous(r[2], is_anon),
        # 匿名帖不暴露真实 author_id
        "author_id": None if is_anon else r[author_idx],
        "author_avatar": None if is_anon else r[avatar_idx],
        "summary": _md_to_text(r[content_idx]),
        "create_time": str(r[3]), "view_count": r[4],
        "is_top": r[5], "is_essence": r[6],
        "is_anonymous": is_anon,
        "is_following": False if is_anon else bool(r[-2]),
        "is_blocked": False if is_anon else bool(r[-1]),
        **({"reply_count": r[reply_idx], "like_count": r[like_idx]}
           if reply_idx is not None and like_idx is not None else {}),
    }


def get_posts_by_board(board_id, page=1, page_size=20, current_user_id=None):
    """按板块获取帖子列表，按发布时间倒序；私密帖永不显示在公共列表（作者在个人中心查看）；匿名帖不暴露 author_id。
    列表卡片所需：作者头像/正文摘要/当前用户关注与拉黑状态（批量子查询，避免 N+1）。"""
    offset = (page - 1) * page_size
    cur_uid = current_user_id or 0
    with get_cursor() as cursor:
        # 公共列表只显示公开帖，私密帖一律不出现（包括作者本人）
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                   p.is_top, p.is_essence, p.is_anonymous, p.author_id,
                   u.avatar_path, p.content, p.like_count,
                   (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count,
                   EXISTS(SELECT 1 FROM user_follows f
                          WHERE f.follower_id = ? AND f.followee_id = p.author_id) AS is_following,
                   EXISTS(SELECT 1 FROM user_blocks b
                          WHERE (b.blocker_id = ? AND b.blocked_id = p.author_id)
                             OR (b.blocker_id = p.author_id AND b.blocked_id = ?)) AS is_blocked
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            WHERE p.board_id = ? AND p.is_private = 0
            ORDER BY p.is_top DESC, p.create_time DESC
            LIMIT ? OFFSET ?
        """, (cur_uid, cur_uid, cur_uid, board_id, page_size, offset))
        rows = cursor.fetchall()
        return [_post_row(r, 7, 8, 9, 10, reply_idx=12, like_idx=11) for r in rows]


def get_hot_posts(page=1, page_size=20, current_user_id=None):
    """获取热门帖子（按点赞数、浏览量排序）；私密帖永不显示在公共列表；匿名帖不暴露 author_id。
    列表卡片所需：作者头像/正文摘要/当前用户关注与拉黑状态（批量子查询，避免 N+1）。"""
    offset = (page - 1) * page_size
    cur_uid = current_user_id or 0
    with get_cursor() as cursor:
        # 公共列表只显示公开帖，私密帖一律不出现
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                   p.is_top, p.is_essence, p.is_anonymous, p.author_id,
                   u.avatar_path, p.content, p.like_count,
                   (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count,
                   EXISTS(SELECT 1 FROM user_follows f
                          WHERE f.follower_id = ? AND f.followee_id = p.author_id) AS is_following,
                   EXISTS(SELECT 1 FROM user_blocks b
                          WHERE (b.blocker_id = ? AND b.blocked_id = p.author_id)
                             OR (b.blocker_id = p.author_id AND b.blocked_id = ?)) AS is_blocked
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            WHERE p.is_private = 0
            ORDER BY p.is_top DESC, p.like_count DESC, p.view_count DESC
            LIMIT ? OFFSET ?
        """, (cur_uid, cur_uid, cur_uid, page_size, offset))
        rows = cursor.fetchall()
        return [_post_row(r, 7, 8, 9, 10, reply_idx=12, like_idx=11) for r in rows]


def get_post_count(board_id=None, current_user_id=None):
    """获取帖子总数；私密帖子仅作者可见，匿名帖正常统计"""
    with get_cursor() as cursor:
        if board_id:
            cursor.execute("SELECT COUNT(*) FROM post WHERE board_id = ? AND is_private = 0", (board_id,))
        else:
            cursor.execute("SELECT COUNT(*) FROM post WHERE is_private = 0")
        return cursor.fetchone()[0]


def create_post(title, content, author_id, board_id, is_private=0, is_anonymous=0, image_path=None, tag_ids=None):
    """创建新帖子，返回 (success, message)；发帖成功后自动分块并更新RAG索引。
    tag_ids: 标签ID列表（发帖路由已用 get_or_create_tag 解析好）。
    事务边界：帖子 INSERT + 标签关联 INSERT 在同一个 with get_cursor() 块内，
    要么全部成功提交，要么全部回滚（保存文章与维护关联表同事务）。
    RAG 分块在事务外执行（分块失败不影响发帖）。"""
    if not title or not content:
        return False, "标题和正文不能为空"
    # 封禁检查（Service 层）：被封禁用户不可发帖
    from ban_service import is_user_banned
    if is_user_banned(author_id)[0]:
        return False, "账号已被封禁，无法执行此操作"
    # 敏感词过滤（参数化 SQL 之前；命中拒绝并审计）
    from word_filter import block_text
    blocked, msg = block_text("%s\n%s" % (title, content), author_id, "post")
    if blocked:
        return False, msg
    try:
        with get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO post (title, content, author_id, board_id, view_count, like_count,
                                  is_top, is_essence, is_private, is_anonymous, image_path)
                VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?)
            """, (title, content, author_id, board_id, is_private, is_anonymous, image_path))
            # pyodbc 不支持 lastrowid，用 SELECT LAST_INSERT_ID() 获取自增ID
            cursor.execute("SELECT LAST_INSERT_ID()")
            post_id = cursor.fetchone()[0]

            # 同事务维护帖子-标签关联表（tag_ids 由路由层解析，此处只写关联）
            if tag_ids:
                for tid in tag_ids:
                    cursor.execute(
                        "INSERT INTO post_tag (post_id, tag_id) VALUES (?, ?)",
                        (post_id, tid),
                    )

        # with 块已 commit，此时帖子对 chunk_post 可见；分块失败不影响发帖
        try:
            from rag_service import chunk_post, invalidate_index
            chunk_post(post_id, title, content)
            invalidate_index()  # 使索引缓存失效，下次问答时自动重建
        except Exception as e:
            print(f"[警告] 帖子 {post_id} 分块/索引更新失败，暂无法被AI检索：{e}")

        return True, "发布成功"
    except Exception as e:
        print(f"[发帖失败] {e}")
        return False, "发布失败，请稍后重试"


def update_post(post_id, title, content, board_id, is_private=0, is_anonymous=0, image_path=None, tag_ids=None):
    """更新帖子内容与标签关联，返回 (success, message)。
    权限由路由层校验（作者本人或管理员）。
    事务边界：帖子 UPDATE + 标签关联替换（DELETE 旧关联 + INSERT 新关联）在同一个
    with get_cursor() 块内，要么全部成功提交，要么全部回滚（保存文章与维护关联表同事务）。
    RAG 分块在事务外刷新：chunk_post 幂等（先删旧块再重建），失败不影响保存。"""
    if not title or not content:
        return False, "标题和正文不能为空"
    try:
        with get_cursor() as cursor:
            # 先确认帖子存在（避免依赖 UPDATE rowcount 在"值未变化"时的驱动差异）
            cursor.execute("SELECT COUNT(*) FROM post WHERE post_id = ?", (post_id,))
            if cursor.fetchone()[0] == 0:
                return False, "帖子不存在或已被删除"
            # 封禁检查（Service 层）：帖子作者被封禁则不可编辑
            cursor.execute("SELECT author_id FROM post WHERE post_id = ?", (post_id,))
            author_row = cursor.fetchone()
            if author_row:
                from ban_service import is_user_banned
                if is_user_banned(author_row[0])[0]:
                    return False, "账号已被封禁，无法执行此操作"
                # 敏感词过滤（参数化 SQL 之前；命中拒绝并审计）
                from word_filter import block_text
                blocked, msg = block_text(
                    "%s\n%s" % (title, content), author_row[0], "post", post_id)
                if blocked:
                    return False, msg
            cursor.execute("""
                UPDATE post SET title = ?, content = ?, board_id = ?, is_private = ?,
                                is_anonymous = ?, image_path = ?, update_time = NOW()
                WHERE post_id = ?
            """, (title, content, board_id, is_private, is_anonymous, image_path, post_id))

            # 同事务替换标签关联：先清空旧关联，再写入新关联
            cursor.execute("DELETE FROM post_tag WHERE post_id = ?", (post_id,))
            if tag_ids:
                for tid in tag_ids:
                    cursor.execute(
                        "INSERT INTO post_tag (post_id, tag_id) VALUES (?, ?)",
                        (post_id, tid),
                    )

        # with 块已 commit，chunk_post 幂等重建该帖分块并刷新索引缓存；失败不影响保存
        try:
            from rag_service import chunk_post, invalidate_index
            chunk_post(post_id, title, content)
            invalidate_index()
        except Exception as e:
            print(f"[警告] 帖子 {post_id} 编辑后分块/索引更新失败，暂无法被AI检索：{e}")

        return True, "修改成功"
    except Exception as e:
        print(f"[编辑失败] {e}")
        return False, "修改失败，请稍后重试"


def get_post_detail(post_id):
    """获取帖子详情（含作者昵称、板块名、头像、图片、收藏数），不存在返回 None"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT p.post_id, p.title, p.content, p.author_id, u.nickname,
                   p.board_id, b.board_name, p.view_count, p.like_count,
                   p.is_top, p.is_essence, p.is_private, p.is_anonymous,
                   p.create_time, p.update_time, u.avatar_path, p.image_path,
                   (SELECT COUNT(*) FROM favorite f WHERE f.post_id = p.post_id) AS favorite_count
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            JOIN board b ON p.board_id = b.board_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "post_id": row[0], "title": row[1], "content": row[2],
            # author_id 仅供内部权限判断（私密帖可见性、帖主本人识别），UI 不得对外展示
            "author_id": row[3],
            "author": _mask_anonymous(row[4], row[12]),
            "author_avatar": None if row[12] else row[15],
            "board_id": row[5], "board_name": row[6],
            "view_count": row[7], "like_count": row[8],
            "is_top": row[9], "is_essence": row[10],
            "is_private": row[11], "is_anonymous": row[12],
            "create_time": str(row[13]), "update_time": str(row[14]),
            "image_path": row[16], "favorite_count": row[17],
        }


def increment_view_count(post_id):
    """浏览量 +1"""
    with get_cursor() as cursor:
        cursor.execute("UPDATE post SET view_count = view_count + 1 WHERE post_id = ?", (post_id,))


def delete_post(post_id):
    """删除帖子（级联删除评论、收藏、点赞、知识分块、通知等关联数据），返回 (success, message)"""
    try:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM post WHERE post_id = ?", (post_id,))
        # 使RAG索引缓存失效，下次检索时自动重建
        try:
            from rag_service import invalidate_index
            invalidate_index()
        except Exception:
            pass
        return True, "删除成功"
    except Exception as e:
        print(f"[删帖失败] {e}")
        return False, "删除失败，请稍后重试"


def get_post_id_by_image_path(image_path):
    """按图片文件名反查所属帖子 ID（serve_post_image 权限校验用）"""
    with get_cursor() as cursor:
        cursor.execute("SELECT post_id FROM post WHERE image_path = ? LIMIT 1", (image_path,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_latest_post_id_by_author(author_id):
    """取作者最新一篇帖子 ID（含私密帖，供 @提及 通知关联；参数化查询）"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT post_id FROM post WHERE author_id = ? ORDER BY post_id DESC LIMIT 1",
            (author_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def search_posts(keyword, limit=20):
    """按标题关键词搜索公开帖子（LIKE匹配），返回帖子列表"""
    with get_cursor() as cursor:
        like = f"%{keyword}%"
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                   p.is_top, p.is_essence, p.is_anonymous, p.author_id,
                   (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count
            FROM post p
            JOIN `user` u ON p.author_id = u.user_id
            WHERE p.is_private = 0 AND p.title LIKE ?
            ORDER BY p.is_top DESC, p.create_time DESC
            LIMIT ?
        """, (like, limit))
        rows = cursor.fetchall()
        return [{
            "post_id": r[0], "title": r[1],
            "author": _mask_anonymous(r[2], r[7]),
            "create_time": str(r[3]), "view_count": r[4],
            "is_top": r[5], "is_essence": r[6],
            "is_anonymous": r[7], "reply_count": r[9],
        } for r in rows]


def search_suggest(keyword, limit=10):
    """搜索建议：只返回标题，用于自动补全下拉"""
    with get_cursor() as cursor:
        like = f"%{keyword}%"
        cursor.execute("""
            SELECT post_id, title FROM post
            WHERE is_private = 0 AND title LIKE ?
            ORDER BY view_count DESC LIMIT ?
        """, (like, limit))
        return [{"post_id": r[0], "title": r[1]} for r in cursor.fetchall()]
