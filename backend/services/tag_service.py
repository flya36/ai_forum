# -*- coding: utf-8 -*-
# ============================================================
# 标签服务：标签 CRUD、帖子-标签关联维护、按标签查帖子
#
# 分层说明：本文件对应"Service 层"，SQL 直接内联（项目惯例，
# 无 XML Mapper），数据库访问统一走 db.get_cursor()（Mapper 封装），
# 返回 dict 列表（即 DTO/VO）。
#
# 并发安全设计：
#   - tag.tag_name 有 UNIQUE 唯一索引（uk_tag_name）
#   - "先查后插 + 捕获唯一约束冲突（SQLSTATE 23xxx）再回查"双保险，
#     并发创建同名标签时只有一个 INSERT 成功，其余拿到已存在的 tag_id
# ============================================================
import pyodbc
from db import get_cursor

# 标签管理列表每页条数
TAG_PAGE_SIZE = 20
# 单帖最多可打标签数（发帖表单同样限制）
MAX_TAGS_PER_POST = 5
# 标签名称最大长度（对应表结构 VARCHAR(50)）
MAX_TAG_LEN = 50


def _is_duplicate_error(e):
    """判断是否为唯一键/主键冲突（pyodbc SQLSTATE 23xxx 系列）"""
    state = None
    try:
        state = e.args[0]
    except (TypeError, IndexError, AttributeError):
        pass
    return isinstance(state, str) and state.startswith("23")


def _normalize_name(tag_name):
    """清洗标签名：去首尾空白；返回 (ok, message, cleaned)"""
    name = (tag_name or "").strip()
    if not name:
        return False, "标签名称不能为空", ""
    if len(name) > MAX_TAG_LEN:
        return False, f"标签名称不能超过{MAX_TAG_LEN}个字符", ""
    return True, "", name


# ============================================================
# 标签 CRUD（管理端）
# ============================================================
def get_all_tags(page=1, page_size=TAG_PAGE_SIZE, keyword=None):
    """标签管理列表：分页 + 按名称模糊搜索 + 每标签关联文章数
    返回 dict（即 VO）：{tags, total, total_pages, page, keyword}"""
    page = page if page and page > 0 else 1
    offset = (page - 1) * page_size
    kw = (keyword or "").strip()

    with get_cursor() as cursor:
        if kw:
            where = "WHERE t.tag_name LIKE ?"
            params = (f"%{kw}%",)
        else:
            where = ""
            params = ()

        cursor.execute(f"""
            SELECT t.tag_id, t.tag_name, t.created_at,
                   (SELECT COUNT(*) FROM post_tag pt WHERE pt.tag_id = t.tag_id) AS post_count
            FROM tag t
            {where}
            ORDER BY t.tag_id DESC
            LIMIT ? OFFSET ?
        """, params + (page_size, offset))
        rows = cursor.fetchall()

        cursor.execute(f"SELECT COUNT(*) FROM tag t {where}", params)
        total = cursor.fetchone()[0]

    tags = [{
        "tag_id": r[0], "tag_name": r[1],
        "created_at": str(r[2]), "post_count": r[3],
    } for r in rows]
    return {
        "tags": tags,
        "total": total,
        "total_pages": 0 if total == 0 else (total + page_size - 1) // page_size,
        "page": page,
        "keyword": kw,
    }


def create_tag(tag_name):
    """新增标签（管理端 / 发帖自动创建共用入口）
    返回 (success: bool, message: str, tag_id: int | None)"""
    ok, msg, name = _normalize_name(tag_name)
    if not ok:
        return False, msg, None

    try:
        with get_cursor() as cursor:
            # 先查重（业务层友好提示）
            cursor.execute("SELECT tag_id FROM tag WHERE tag_name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return False, f"标签『{name}』已存在", None
            cursor.execute("INSERT INTO tag (tag_name) VALUES (?)", (name,))
            cursor.execute("SELECT LAST_INSERT_ID()")
            tag_id = cursor.fetchone()[0]
        return True, "标签添加成功", tag_id
    except pyodbc.Error as e:
        # 并发兜底：唯一索引冲突 → 回查已存在标签。
        # 对管理端 create_tag 而言此时算"已存在"失败，不返回 id
        # （get_or_create_tag 才是成功复用语义，见其独立实现）
        if _is_duplicate_error(e):
            try:
                with get_cursor() as cursor:
                    cursor.execute("SELECT tag_id FROM tag WHERE tag_name = ?", (name,))
                    row = cursor.fetchone()
                if row:
                    return False, f"标签『{name}』已存在", None
            except Exception:
                pass
        print(f"[标签创建失败] {e}")
        return False, "标签添加失败，请稍后重试", None
    except Exception as e:
        print(f"[标签创建失败] {e}")
        return False, "标签添加失败，请稍后重试", None


def update_tag(tag_id, tag_name):
    """修改标签名称（唯一校验需排除自身）
    返回 (success: bool, message: str)"""
    ok, msg, name = _normalize_name(tag_name)
    if not ok:
        return False, msg

    try:
        with get_cursor() as cursor:
            # 重名校验：排除当前标签自身
            cursor.execute("SELECT tag_id FROM tag WHERE tag_name = ? AND tag_id != ?", (name, tag_id))
            if cursor.fetchone():
                return False, f"标签『{name}』已存在"
            cursor.execute("UPDATE tag SET tag_name = ? WHERE tag_id = ?", (name, tag_id))
            if cursor.rowcount == 0:
                return False, "标签不存在或已被删除"
        return True, "标签已更新"
    except pyodbc.Error as e:
        if _is_duplicate_error(e):
            return False, f"标签『{name}』已存在"
        print(f"[标签更新失败] {e}")
        return False, "标签更新失败，请稍后重试"
    except Exception as e:
        print(f"[标签更新失败] {e}")
        return False, "标签更新失败，请稍后重试"


def delete_tag(tag_id):
    """删除标签：有关联文章时拒绝删除（提示解除关联）
    返回 (success: bool, message: str)"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT tag_name FROM tag WHERE tag_id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                return False, "标签不存在或已被删除"

            # 关联校验：有文章引用则禁止删除
            cursor.execute("SELECT COUNT(*) FROM post_tag WHERE tag_id = ?", (tag_id,))
            cnt = cursor.fetchone()[0]
            if cnt > 0:
                return False, f"标签『{row[0]}』已被 {cnt} 篇文章使用，无法删除；请先在相关文章中解除该标签"

            cursor.execute("DELETE FROM tag WHERE tag_id = ?", (tag_id,))
        return True, "标签已删除"
    except Exception as e:
        print(f"[标签删除失败] {e}")
        return False, "标签删除失败，请稍后重试"


# ============================================================
# 发帖/编辑时使用
# ============================================================
def get_or_create_tag(tag_name):
    """发帖时使用：标签已存在则复用，不存在则创建。
    独立小事务（自动 commit），唯一索引兜底并发创建同名标签。
    返回 (success: bool, message: str, tag_id: int | None)"""
    ok, msg, name = _normalize_name(tag_name)
    if not ok:
        return False, msg, None

    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT tag_id FROM tag WHERE tag_name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return True, "ok", row[0]
            cursor.execute("INSERT INTO tag (tag_name) VALUES (?)", (name,))
            cursor.execute("SELECT LAST_INSERT_ID()")
            tag_id = cursor.fetchone()[0]
        return True, "ok", tag_id
    except pyodbc.Error as e:
        if _is_duplicate_error(e):
            # 并发创建同名：另一个请求刚插入成功，回查复用
            try:
                with get_cursor() as cursor:
                    cursor.execute("SELECT tag_id FROM tag WHERE tag_name = ?", (name,))
                    row = cursor.fetchone()
                if row:
                    return True, "ok", row[0]
            except Exception:
                pass
        print(f"[标签创建失败] {e}")
        return False, "标签创建失败", None
    except Exception as e:
        print(f"[标签创建失败] {e}")
        return False, "标签创建失败", None


def replace_post_tags(post_id, tag_ids):
    """编辑文章时同步维护关联：删除旧关联 + 写入新关联，同一事务。
    tag_ids 为空列表表示清空该文章全部标签。
    返回 (success: bool, message: str)"""
    try:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM post_tag WHERE post_id = ?", (post_id,))
            if tag_ids:
                for tid in tag_ids:
                    cursor.execute(
                        "INSERT INTO post_tag (post_id, tag_id) VALUES (?, ?)",
                        (post_id, tid),
                    )
        return True, "标签已更新"
    except Exception as e:
        print(f"[标签关联更新失败] {e}")
        return False, "标签更新失败，请稍后重试"


# ============================================================
# 前台展示
# ============================================================
def get_tag_by_id(tag_id):
    """按ID获取单个标签信息（标签列表页标题用），不存在返回 None"""
    with get_cursor() as cursor:
        cursor.execute("SELECT tag_id, tag_name, created_at FROM tag WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {"tag_id": row[0], "tag_name": row[1], "created_at": str(row[2])}


def get_tags_by_post(post_id):
    """获取某篇文章的全部标签（详情页展示）"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT t.tag_id, t.tag_name
            FROM post_tag pt
            JOIN tag t ON pt.tag_id = t.tag_id
            WHERE pt.post_id = ?
            ORDER BY t.tag_id
        """, (post_id,))
        return [{"tag_id": r[0], "tag_name": r[1]} for r in cursor.fetchall()]


def get_posts_by_tag(tag_id, page=1, page_size=TAG_PAGE_SIZE):
    """标签下的文章列表（分页）：只显示公开帖，私密帖不出现；
    匿名帖作者掩码为"匿名用户"，不暴露真实 author_id。
    返回 (posts: list, total: int, total_pages: int)"""
    page = page if page and page > 0 else 1
    offset = (page - 1) * page_size

    with get_cursor() as cursor:
        cursor.execute("""
            SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                   p.is_top, p.is_essence, p.is_anonymous, p.author_id,
                   (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count
            FROM post_tag pt
            JOIN post p ON pt.post_id = p.post_id
            JOIN `user` u ON p.author_id = u.user_id
            WHERE pt.tag_id = ? AND p.is_private = 0
            ORDER BY p.is_top DESC, p.create_time DESC
            LIMIT ? OFFSET ?
        """, (tag_id, page_size, offset))
        rows = cursor.fetchall()

        cursor.execute("""
            SELECT COUNT(*)
            FROM post_tag pt
            JOIN post p ON pt.post_id = p.post_id
            WHERE pt.tag_id = ? AND p.is_private = 0
        """, (tag_id,))
        total = cursor.fetchone()[0]

    posts = [{
        "post_id": r[0], "title": r[1],
        "author": "匿名用户" if r[7] else r[2],
        "author_id": None if r[7] else r[8],
        "create_time": str(r[3]), "view_count": r[4],
        "is_top": r[5], "is_essence": r[6], "is_anonymous": r[7],
        "reply_count": r[9],
    } for r in rows]
    return posts, total, max(1, (total + page_size - 1) // page_size)


def get_all_tags_simple():
    """全部标签简表（发帖页"选择已有标签"组件初始化）"""
    with get_cursor() as cursor:
        cursor.execute("SELECT tag_id, tag_name FROM tag ORDER BY tag_id")
        return [{"tag_id": r[0], "tag_name": r[1]} for r in cursor.fetchall()]


def search_tags_suggest(keyword, limit=8):
    """标签搜索建议（发帖页输入时提示已有标签，避免重复创建）"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT tag_id, tag_name FROM tag WHERE tag_name LIKE ? ORDER BY tag_id LIMIT ?",
            (f"%{kw}%", limit),
        )
        return [{"tag_id": r[0], "tag_name": r[1]} for r in cursor.fetchall()]
