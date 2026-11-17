# ============================================================
# 板块服务：板块的数据库查询操作
# ============================================================
import pyodbc
from db import get_cursor
from notification_service import add_notification

# 板块名称最大长度（对应表结构 VARCHAR(50)）
MAX_BOARD_NAME_LEN = 50
# 板块描述最大长度（对应表结构 VARCHAR(255)）
MAX_DESC_LEN = 255
# 管理端申请列表每页条数
APP_PAGE_SIZE = 20
# 同用户最多同时存在的待审核申请数（频率限制）
MAX_PENDING_PER_USER = 3
# 同用户 1 小时内最多提交申请数（频率限制）
MAX_APPLY_PER_HOUR = 3


def _is_duplicate_error(e):
    """判断是否为唯一键/主键冲突（pyodbc SQLSTATE 23xxx 系列）"""
    state = None
    try:
        state = e.args[0]
    except (TypeError, IndexError, AttributeError):
        pass
    return isinstance(state, str) and state.startswith("23")


def _normalize_board_name(board_name):
    """清洗板块名：去首尾空白；返回 (ok, message, cleaned)"""
    name = (board_name or "").strip()
    if not name:
        return False, "板块名称不能为空", ""
    if len(name) > MAX_BOARD_NAME_LEN:
        return False, f"板块名称不能超过{MAX_BOARD_NAME_LEN}个字符", ""
    return True, "", name


def get_all_boards():
    """获取所有板块，返回列表 [{board_id, board_name, description}]"""
    with get_cursor() as cursor:
        cursor.execute("SELECT board_id, board_name, description FROM board ORDER BY board_id")
        rows = cursor.fetchall()
        return [{"board_id": r[0], "board_name": r[1], "description": r[2]} for r in rows]


def ensure_default_boards():
    """如果板块表为空，插入默认板块（首次运行时自动初始化）"""
    with get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM board")
        count = cursor.fetchone()[0]
        if count == 0:
            defaults = [
                ("技术交流", "编程技术、开发经验分享"),
                ("问答求助", "遇到问题来这里提问"),
                ("资源分享", "学习资源、工具推荐"),
                ("闲聊灌水", "日常交流、话题讨论"),
            ]
            for name, desc in defaults:
                cursor.execute(
                    "INSERT INTO board (board_name, description) VALUES (?, ?)",
                    (name, desc),
                )


# ============================================================
# 板块 CRUD（管理端）
# ============================================================
def get_board_post_count(board_id):
    """统计某板块下的帖子数（删除板块前的关联校验）"""
    with get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM post WHERE board_id = ?", (board_id,))
        return cursor.fetchone()[0]


def top_boards(limit=5):
    """热门板块排行榜：按板块内公开帖数量排序，取 Top N（参数化 SQL）。
    私密帖不计入，保证排行榜仅反映可见内容。"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT b.board_id, b.board_name,
                   (SELECT COUNT(*) FROM post p
                     WHERE p.board_id = b.board_id AND p.is_private = 0) AS post_count
            FROM board b
            ORDER BY post_count DESC, b.board_id ASC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [{"board_id": r[0], "board_name": r[1], "post_count": r[2]} for r in rows]


def create_board(name, description):
    """新增板块（管理端）。返回 (success, message)
    校验：空值/长度/去空格/重名；board_name 唯一索引兜底并发创建同名板块。"""
    ok, msg, bname = _normalize_board_name(name)
    if not ok:
        return False, msg
    desc = (description or "").strip()
    if len(desc) > MAX_DESC_LEN:
        return False, f"板块描述不能超过{MAX_DESC_LEN}个字符"
    try:
        with get_cursor() as cursor:
            # 先查重（业务层友好提示）
            cursor.execute("SELECT board_id FROM board WHERE board_name = ?", (bname,))
            if cursor.fetchone():
                return False, f"板块『{bname}』已存在"
            cursor.execute(
                "INSERT INTO board (board_name, description) VALUES (?, ?)",
                (bname, desc),
            )
        return True, "板块创建成功"
    except pyodbc.Error as e:
        if _is_duplicate_error(e):
            return False, f"板块『{bname}』已存在"
        print(f"[板块创建失败] {e}")
        return False, "板块创建失败，请稍后重试"
    except Exception as e:
        print(f"[板块创建失败] {e}")
        return False, "板块创建失败，请稍后重试"


def update_board(board_id, name, description):
    """编辑板块名称/描述（管理端）。返回 (success, message)
    校验：存在性、重名（排除自身）、长度。"""
    ok, msg, bname = _normalize_board_name(name)
    if not ok:
        return False, msg
    desc = (description or "").strip()
    if len(desc) > MAX_DESC_LEN:
        return False, f"板块描述不能超过{MAX_DESC_LEN}个字符"
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT board_id FROM board WHERE board_id = ?", (board_id,))
            if not cursor.fetchone():
                return False, "板块不存在或已被删除"
            # 重名校验：排除当前板块自身
            cursor.execute("SELECT board_id FROM board WHERE board_name = ? AND board_id != ?", (bname, board_id))
            if cursor.fetchone():
                return False, f"板块『{bname}』已存在"
            cursor.execute(
                "UPDATE board SET board_name = ?, description = ? WHERE board_id = ?",
                (bname, desc, board_id),
            )
        return True, "板块已更新"
    except pyodbc.Error as e:
        if _is_duplicate_error(e):
            return False, f"板块『{bname}』已存在"
        print(f"[板块更新失败] {e}")
        return False, "板块更新失败，请稍后重试"
    except Exception as e:
        print(f"[板块更新失败] {e}")
        return False, "板块更新失败，请稍后重试"


def delete_board(board_id):
    """删除板块（管理端）。返回 (success, message)
    关联校验：板块下有帖子（post.board_id 外键 ON DELETE CASCADE）时禁止删除，
    必须先转移或删除该板块帖子，避免级联误删。无帖子才删除。"""
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT board_name FROM board WHERE board_id = ?", (board_id,))
            row = cursor.fetchone()
            if not row:
                return False, "板块不存在或已被删除"
            cnt = get_board_post_count(board_id)
            if cnt > 0:
                return False, f"板块『{row[0]}』下还有 {cnt} 篇帖子，无法删除；请先转移或删除这些帖子"
            cursor.execute("DELETE FROM board WHERE board_id = ?", (board_id,))
        # 删除后使RAG索引缓存失效（级联删除的帖子已无，保底刷新）
        try:
            from rag_service import invalidate_index
            invalidate_index()
        except Exception:
            pass
        return True, "板块已删除"
    except Exception as e:
        print(f"[板块删除失败] {e}")
        return False, "板块删除失败，请稍后重试"


# ============================================================
# 用户申请新板块 + 管理员审核
# ============================================================
def apply_board(user_id, board_name, description):
    """用户申请新板块。返回 (success, message)
    校验：与现有板块重名、与待审核申请重名、长度、频率限制（待审核数/小时申请数）。"""
    ok, msg, bname = _normalize_board_name(board_name)
    if not ok:
        return False, msg
    desc = (description or "").strip()
    if len(desc) > MAX_DESC_LEN:
        return False, f"描述不能超过{MAX_DESC_LEN}个字符"
    try:
        with get_cursor() as cursor:
            # 与现有板块重名
            cursor.execute("SELECT board_id FROM board WHERE board_name = ?", (bname,))
            if cursor.fetchone():
                return False, f"板块『{bname}』已存在，无需重复申请"
            # 与任何待审核申请重名（其他用户申请中也不行，避免重复建板块）
            cursor.execute(
                "SELECT application_id FROM board_application WHERE board_name = ? AND status = 0",
                (bname,),
            )
            if cursor.fetchone():
                return False, f"板块『{bname}』已有待审核申请，请勿重复申请"
            # 频率限制：同用户待审核申请数
            cursor.execute(
                "SELECT COUNT(*) FROM board_application WHERE user_id = ? AND status = 0",
                (user_id,),
            )
            if cursor.fetchone()[0] >= MAX_PENDING_PER_USER:
                return False, f"你已有 {MAX_PENDING_PER_USER} 条待审核申请，请等待管理员处理"
            # 频率限制：同用户 1 小时内申请数
            cursor.execute(
                "SELECT COUNT(*) FROM board_application WHERE user_id = ? AND apply_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)",
                (user_id,),
            )
            if cursor.fetchone()[0] >= MAX_APPLY_PER_HOUR:
                return False, "申请过于频繁，请稍后再试"
            cursor.execute(
                "INSERT INTO board_application (user_id, board_name, description) VALUES (?, ?, ?)",
                (user_id, bname, desc),
            )
        return True, "申请已提交，请等待管理员审核"
    except Exception as e:
        print(f"[板块申请失败] {e}")
        return False, "申请失败，请稍后重试"


def get_applications(status=None, page=1, page_size=APP_PAGE_SIZE):
    """管理端：申请列表（分页，含申请人昵称/审核人昵称）
    返回 dict：{applications, total, total_pages, page, status}"""
    page = page if page and page > 0 else 1
    offset = (page - 1) * page_size
    where = ""
    params = ()
    if status is not None:
        where = "WHERE ba.status = ?"
        params = (status,)
    with get_cursor() as cursor:
        cursor.execute(f"""
            SELECT ba.application_id, ba.board_name, ba.description, ba.status,
                   ba.apply_time, ba.review_time, ba.reject_reason,
                   u.nickname, rv.nickname
            FROM board_application ba
            JOIN `user` u ON ba.user_id = u.user_id
            LEFT JOIN `user` rv ON ba.reviewer_id = rv.user_id
            {where}
            ORDER BY ba.apply_time DESC
            LIMIT ? OFFSET ?
        """, params + (page_size, offset))
        rows = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) FROM board_application ba {where}", params)
        total = cursor.fetchone()[0]
    applications = [{
        "application_id": r[0], "board_name": r[1], "description": r[2],
        "status": r[3], "apply_time": str(r[4]),
        "review_time": str(r[5]) if r[5] else None,
        "reject_reason": r[6], "applicant": r[7], "reviewer": r[8],
    } for r in rows]
    return {
        "applications": applications,
        "total": total,
        "total_pages": 0 if total == 0 else (total + page_size - 1) // page_size,
        "page": page,
        "status": status,
    }


def get_applications_by_user(user_id):
    """用户端：某用户的全部申请记录（最新在前）"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT application_id, board_name, description, status,
                   apply_time, review_time, reject_reason
            FROM board_application WHERE user_id = ?
            ORDER BY apply_time DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return [{
            "application_id": r[0], "board_name": r[1], "description": r[2],
            "status": r[3], "apply_time": str(r[4]),
            "review_time": str(r[5]) if r[5] else None,
            "reject_reason": r[6],
        } for r in rows]


def review_application(application_id, reviewer_id, approve, reject_reason=None):
    """管理员审核板块申请。返回 (success, message)
    - 只允许审核 status=0 的申请；用条件更新（WHERE status=0）防止并发重复审核
    - 通过：同事务 更新申请状态(0→1) + 创建板块；板块名冲突则整体回滚
    - 驳回：必须填原因，写 reject_reason 并置状态(0→2)
    - 通知在事务外（通知失败不影响审核结果，符合项目"外挂能力失败不阻断主流程"惯例）"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id, board_name, description FROM board_application WHERE application_id = ?",
                (application_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False, "申请不存在"
            applicant_id, bname, bdesc = row[0], row[1], row[2]

            if approve:
                # 先查重（业务层友好提示；并发兜底靠 uk_board_name 唯一索引）
                cursor.execute("SELECT board_id FROM board WHERE board_name = ?", (bname,))
                if cursor.fetchone():
                    return False, f"板块『{bname}』已存在，无法通过审核"
                cursor.execute("""
                    UPDATE board_application
                    SET status = 1, review_time = NOW(), reviewer_id = ?, reject_reason = NULL
                    WHERE application_id = ? AND status = 0
                """, (reviewer_id, application_id))
                if cursor.rowcount == 0:
                    return False, "该申请已被处理，请勿重复审核"
                cursor.execute(
                    "INSERT INTO board (board_name, description) VALUES (?, ?)",
                    (bname, bdesc),
                )
            else:
                reason = (reject_reason or "").strip()
                if not reason:
                    return False, "驳回原因不能为空"
                if len(reason) > MAX_DESC_LEN:
                    return False, f"驳回原因不能超过{MAX_DESC_LEN}个字符"
                cursor.execute("""
                    UPDATE board_application
                    SET status = 2, review_time = NOW(), reviewer_id = ?, reject_reason = ?
                    WHERE application_id = ? AND status = 0
                """, (reviewer_id, reason, application_id))
                if cursor.rowcount == 0:
                    return False, "该申请已被处理，请勿重复审核"

        # with 块已 commit；通知申请人（失败仅打印，不影响审核结果）
        try:
            if approve:
                add_notification(
                    user_id=applicant_id, notify_type="board", post_id=None,
                    from_user_id=reviewer_id,
                    content=f"你申请的板块『{bname}』已通过审核，现在可以发帖了",
                )
            else:
                add_notification(
                    user_id=applicant_id, notify_type="board", post_id=None,
                    from_user_id=reviewer_id,
                    content=f"你申请的板块『{bname}』未通过审核，原因：{reason}",
                )
        except Exception as e:
            print(f"[板块申请通知失败] {e}")

        return (True, "已通过审核") if approve else (True, "已驳回申请")
    except pyodbc.Error as e:
        if _is_duplicate_error(e):
            return False, f"板块『{bname}』已存在，无法通过审核"
        print(f"[审核板块申请失败] {e}")
        return False, "审核失败，请稍后重试"
    except Exception as e:
        print(f"[审核板块申请失败] {e}")
        return False, "审核失败，请稍后重试"
