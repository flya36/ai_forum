# -*- coding: utf-8 -*-
# ============================================================
# 举报 / 申诉工单服务（report_tickets 表）
#
# 设计：
#   - 举报（post/comment）与申诉（appeal）统一为"审核工单"，管理端共用审核队列
#   - 重复约束：同一用户对同一目标仅允许一条 pending 工单（应用层检查）
#   - 所有 SQL 参数化；审计与通知由路由层负责（本服务保持纯数据操作）
#   - 处理动作：举报 approve=删除目标内容 / approve_ban=删除+封禁作者 / reject=驳回；
#     申诉 approve=解封 / reject=维持封禁
# ============================================================
from db import get_cursor
from notification_service import add_notification

VALID_TARGET_TYPES = ("post", "comment", "appeal")
VALID_CATEGORIES = ("violation", "ad", "porn", "other", "appeal")

CATEGORY_LABELS = {
    "violation": "违规", "ad": "广告", "porn": "色情",
    "other": "其他", "appeal": "申诉",
}


def _has_pending(reporter_id, target_type, target_id):
    """同一用户对同一目标是否已有 pending 工单"""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM report_tickets "
            "WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'pending'",
            (reporter_id, target_type, target_id),
        )
        return cursor.fetchone()[0] > 0


def _list_admin_ids():
    """参数化 SQL：查询所有管理员（role=1）用户 ID，用于新工单通知"""
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id FROM `user` WHERE role = 1")
        return [r[0] for r in cursor.fetchall()]


def _notify_admins_new_ticket(content, from_user_id, ticket_type):
    """向所有管理员发送新工单站内通知（type=new_ticket）并实时广播 SocketIO 事件。
    content 按工单类型区分：举报（帖子/评论）/ 申诉。触发者恰为管理员时不通知自身。
    ticket_type: 'report' / 'appeal'（用于前端 Toast 与红点事件负载）。"""
    for aid in _list_admin_ids():
        add_notification(
            user_id=aid, notify_type="new_ticket",
            post_id=None, from_user_id=from_user_id, content=content)
    # 实时推送：向 admins 房间广播（socketio_bus 共享实例 = app 运行中实例；
    # 不能用 `from app import socketio`——app.py 以 __main__ 运行时该 import 会加载副本实例）
    try:
        from socketio_bus import socketio as _sio
        _sio.emit(
            "new_ticket_alert",
            {"message": "有新的审核工单待处理", "ticket_type": ticket_type},
            room="admins",
        )
    except Exception as e:
        print(f"[新工单实时推送失败] {e}")


def _report_target_desc(target_type, target_id):
    """工单目标描述（帖子标题 / 评论内容前 30 字），用于管理员通知文案"""
    try:
        with get_cursor() as cursor:
            if target_type == "post":
                cursor.execute("SELECT title FROM post WHERE post_id = ?", (target_id,))
                row = cursor.fetchone()
                return ("帖子《%s》" % row[0]) if row else "帖子"
            cursor.execute("SELECT content FROM comment WHERE comment_id = ?", (target_id,))
            row = cursor.fetchone()
            return ("评论：%s" % row[0][:30]) if row else "评论"
    except Exception:
        return target_type


def create_report(reporter_id, target_type, target_id, category="other", description=""):
    """提交举报。返回 (success, message)。"""
    if target_type not in ("post", "comment"):
        return False, "举报类型不合法"
    if category not in VALID_CATEGORIES:
        category = "other"
    description = (description or "").strip()[:500]
    # 校验目标存在
    try:
        with get_cursor() as cursor:
            if target_type == "post":
                cursor.execute("SELECT COUNT(*) FROM post WHERE post_id = ?", (target_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM comment WHERE comment_id = ?", (target_id,))
            if cursor.fetchone()[0] == 0:
                return False, "目标内容不存在或已被删除"
        # 重复举报拦截（同一用户对同一目标仅一条 pending）
        if _has_pending(reporter_id, target_type, target_id):
            return False, "您已举报过该内容，请勿重复提交"
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO report_tickets (reporter_id, target_type, target_id, category, description, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (reporter_id, target_type, target_id, category, description),
            )
        # 双向通知：新工单提交即通知所有管理员（参数化查询管理员列表）+ 实时推送
        prefix = "有新的帖子举报待处理" if target_type == "post" else "有新的评论举报待处理"
        _notify_admins_new_ticket(
            "%s（目标：%s）" % (prefix, _report_target_desc(target_type, target_id)),
            reporter_id, "report")
        return True, "举报已提交，感谢您的反馈"
    except Exception as e:
        print(f"[提交举报失败] {e}")
        return False, "提交失败，请稍后重试"


def create_appeal(reporter_id, username, reason):
    """提交封禁申诉（允许未登录：校验用户名对应账号确实处于封禁状态）。
    防探测：账号不存在 / 未封禁 / 已申诉 三种失败均返回统一模糊提示，
    不暴露该账号是否存在、是否被封禁。返回 (success, message, target_user_id)。"""
    reason = (reason or "").strip()[:500]
    if not username.strip() or not reason:
        return False, "用户名和申诉理由不能为空", None
    # 统一模糊提示（防账号状态探测）
    BLUR_MSG = "申诉请求已收到，如果账号存在且被封禁，管理员将会处理"
    try:
        from ban_service import is_user_banned
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM `user` WHERE username = ?", (username.strip(),))
            row = cursor.fetchone()
            if not row:
                return False, BLUR_MSG, None
            target_user_id = row[0]
        banned, _, _ = is_user_banned(target_user_id)
        if not banned:
            return False, BLUR_MSG, target_user_id
        # 同一用户仅允许一条 pending 申诉（以被申诉账号维度去重，reporter_id 同 target_id）
        if _has_pending(target_user_id, "appeal", target_user_id):
            return False, BLUR_MSG, target_user_id
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO report_tickets (reporter_id, target_type, target_id, category, description, status) "
                "VALUES (?, 'appeal', ?, 'appeal', ?, 'pending')",
                (target_user_id, target_user_id, reason),
            )
        # 双向通知：申诉提交即通知所有管理员（不暴露申诉者身份之外的信息）+ 实时推送
        _notify_admins_new_ticket(
            "有新的账号申诉待处理（用户：%s）" % username.strip(), target_user_id, "appeal")
        return True, "申诉已提交，请耐心等待处理", target_user_id
    except Exception as e:
        print(f"[提交申诉失败] {e}")
        return False, BLUR_MSG, None


def get_pending_tickets(page=1, page_size=20):
    """管理端审核队列：所有 pending 工单，按时间倒序，附带目标内容片段。
    分页：LIMIT/OFFSET 参数化（page>=1，page_size 上限 50，防恶意大页拖库）。"""
    if page < 1:
        page = 1
    page_size = max(1, min(int(page_size), 50))
    offset = (page - 1) * page_size
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM report_tickets WHERE status = 'pending'")
        total = cursor.fetchone()[0]
        cursor.execute("""
            SELECT t.id, t.reporter_id, t.target_type, t.target_id, t.category,
                   t.description, t.status, t.created_at, u.nickname
            FROM report_tickets t
            LEFT JOIN `user` u ON t.reporter_id = u.user_id
            WHERE t.status = 'pending'
            ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset))
        rows = cursor.fetchall()
        tickets = []
        for r in rows:
            t = {
                "id": r[0], "reporter_id": r[1], "target_type": r[2],
                "target_id": r[3], "category": r[4],
                "description": r[5], "status": r[6],
                "created_at": str(r[7]),
                "reporter_name": r[8] or ("用户#%s" % r[1]),
                "category_label": CATEGORY_LABELS.get(r[4], r[4]),
                "snippet": "",
            }
            t["snippet"] = _target_snippet(cursor, t["target_type"], t["target_id"])
            tickets.append(t)
        return tickets, total


def _target_snippet(cursor, target_type, target_id):
    """目标内容片段（帖子标题 / 评论内容前 60 字 / 申诉账号信息）"""
    try:
        if target_type == "post":
            cursor.execute("SELECT title FROM post WHERE post_id = ?", (target_id,))
            row = cursor.fetchone()
            return ("帖子《%s》" % row[0]) if row else "（帖子已被删除）"
        if target_type == "comment":
            cursor.execute("SELECT content FROM comment WHERE comment_id = ?", (target_id,))
            row = cursor.fetchone()
            return ("评论：%s" % row[0][:60]) if row else "（评论已被删除）"
        if target_type == "appeal":
            cursor.execute(
                "SELECT username, status, ban_reason, ban_until FROM `user` WHERE user_id = ?",
                (target_id,))
            row = cursor.fetchone()
            if not row:
                return "（用户不存在）"
            until = str(row[3])[:16] if row[3] else "永久"
            return "用户 %s（封禁中，原因：%s，禁至 %s）" % (row[0], row[2] or "-", until)
    except Exception as e:
        print(f"[工单目标片段查询失败] {e}")
    return ""


def get_my_tickets(user_id, limit=50):
    """普通用户查看自己的举报/申诉记录（不得见管理端队列）"""
    with get_cursor() as cursor:
        cursor.execute("""
            SELECT id, target_type, target_id, category, description, status, created_at
            FROM report_tickets
            WHERE reporter_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "target_type": r[1], "target_id": r[2],
            "category": CATEGORY_LABELS.get(r[3], r[3]),
            "description": r[4], "status": r[5], "created_at": str(r[6]),
        } for r in rows]


def handle_report(ticket_id, admin_id, action):
    """管理员处理举报工单。
    action: approve=删除目标内容 / approve_ban=删除+封禁目标作者 / reject=驳回
    返回 (success, message, ticket_info)。审计与通知由路由层负责。"""
    try:
        from ban_service import ban_user
        from admin_service import delete_post as admin_delete_post
        from admin_service import delete_comment as admin_delete_comment
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, reporter_id, target_type, target_id, status FROM report_tickets WHERE id = ?",
                (ticket_id,))
            row = cursor.fetchone()
            if not row:
                return False, "工单不存在", None
            if row[4] != "pending":
                return False, "工单已处理", None
            ticket = {"id": row[0], "reporter_id": row[1],
                      "target_type": row[2], "target_id": row[3]}

        target_author_id = None
        if action == "reject":
            with get_cursor() as cursor:
                cursor.execute(
                    "UPDATE report_tickets SET status = 'rejected', handled_by = ?, handled_at = NOW() WHERE id = ?",
                    (admin_id, ticket_id))
            return True, "已驳回该举报", ticket

        # approve / approve_ban：删除目标内容（先取目标作者，删除后再查会查不到）
        if ticket["target_type"] == "post":
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT author_id, title FROM post WHERE post_id = ?", (ticket["target_id"],))
                row = cursor.fetchone()
                target_author_id = row[0] if row else None
                target_title = row[1] if row else ""
            # 通知帖主"因违规被删除"（不透露举报人身份：from_user_id=处理管理员）
            notify_content = f"你的帖子因违规被删除：{target_title}" if target_title else None
            ok, msg = admin_delete_post(
                ticket["target_id"], operator_id=admin_id, notify_content=notify_content)
            if not ok:
                return False, "删除帖子失败：%s" % msg, ticket
        else:  # comment
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM comment WHERE comment_id = ?", (ticket["target_id"],))
                row = cursor.fetchone()
                target_author_id = row[0] if row else None
            ok, msg = admin_delete_comment(ticket["target_id"])
            if not ok:
                return False, "删除评论失败：%s" % msg, ticket

        if action == "approve_ban" and target_author_id:
            # 通过并封禁目标作者（原因默认取工单描述，时长 7 天；失败不阻断主流程）
            try:
                ban_user(target_author_id, "举报审核通过（自动封禁）", 7, admin_id)
            except Exception as e:
                print(f"[举报联动封禁失败] {e}")

        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE report_tickets SET status = 'resolved', handled_by = ?, handled_at = NOW() WHERE id = ?",
                (admin_id, ticket_id))
        return True, "已处理该举报", ticket
    except Exception as e:
        print(f"[处理举报工单失败] {e}")
        return False, "处理失败，请稍后重试", None


def handle_appeal(ticket_id, admin_id, action):
    """管理员处理申诉工单。
    action: approve=解封 / reject=维持封禁。返回 (success, message, ticket_info)。"""
    try:
        from ban_service import unban_user
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, reporter_id, target_type, target_id, status FROM report_tickets WHERE id = ?",
                (ticket_id,))
            row = cursor.fetchone()
            if not row:
                return False, "工单不存在", None
            if row[4] != "pending":
                return False, "工单已处理", None
            ticket = {"id": row[0], "reporter_id": row[1],
                      "target_type": row[2], "target_id": row[3]}

        if action == "approve":
            ok, msg = unban_user(ticket["target_id"], admin_id)
            if not ok:
                return False, "解封失败：%s" % msg, ticket
            new_status = "resolved"
            # 通知触达：解封通知落库，被封禁用户解封后可登录在通知中心看到
            add_notification(
                user_id=ticket["target_id"], notify_type="appeal",
                post_id=None, from_user_id=admin_id,
                content="申诉已通过，账号已解封，请重新登录",
            )
        else:
            new_status = "rejected"
            add_notification(
                user_id=ticket["target_id"], notify_type="appeal",
                post_id=None, from_user_id=admin_id,
                content="申诉未通过，账号维持封禁状态",
            )
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE report_tickets SET status = ?, handled_by = ?, handled_at = NOW() WHERE id = ?",
                (new_status, admin_id, ticket_id))
        return True, "已处理该申诉", ticket
    except Exception as e:
        print(f"[处理申诉工单失败] {e}")
        return False, "处理失败，请稍后重试", None
