# -*- coding: utf-8 -*-
# ============================================================
# 审计日志模块：记录管理员/敏感操作，支撑"谁在何时对什么数据做了什么"
# 依赖表：audit_log（见 database/migrations/add_audit_log.sql）
# 设计：审计失败不阻断业务（best-effort），但与业务事务分离、独立落库，
#       避免业务回滚连带审计丢失。审计表仅 INSERT，不允许应用层 UPDATE/DELETE。
# ============================================================
from db import get_cursor

# 允许的操作类型（白名单，防止误用/注入无关 action 值）
ALLOWED_ACTIONS = {
    "view_admin",          # 查看管理后台（含 tab）
    "toggle_user_status",  # 启用/禁用用户
    "toggle_post_top",     # 置顶
    "toggle_post_essence", # 加精
    "delete_post",         # 删除帖子
    "delete_comment",      # 删除评论
    "create_tag", "update_tag", "delete_tag",
    "create_board", "update_board", "delete_board",
    "review_approve", "review_reject",  # 板块申请审核
    "delete_account",      # 用户注销账号（隐私删除闭环）
    "change_password",     # 用户自助修改密码
    "ban_user",            # 管理员封禁用户
    "unban_user",          # 管理员解封用户
    "login_blocked",       # 被封禁用户尝试登录被拒
    "sensitive_word_block",  # 发布内容命中敏感词被拦截
    "handle_report",      # 管理员处理举报工单（通过/通过并封禁/驳回）
    "handle_appeal",      # 管理员处理申诉工单（解封/维持封禁）
    "view_post_detail",   # 管理员查看私密/匿名帖详情（隐私审核留痕）
    "toggle_ai_reply",    # 管理员开关 AI 智能回复/设置每日上限
    "revoke_ai_reply",    # 管理员撤回 AI 助手评论
    "block_user",         # 用户拉黑他人
    "unblock_user",       # 用户解除拉黑
}


def log_audit(operator_id, action, target_type, target_id=None, detail=""):
    """写入一条审计记录。
    operator_id: 操作者用户ID
    action:      操作类型（须在 ALLOWED_ACTIONS 白名单）
    target_type: 目标类型（user/post/comment/tag/board/board_application）
    target_id:   目标ID（可为空）
    detail:      附加说明（截断到 500 字符）
    """
    if action not in ALLOWED_ACTIONS:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO audit_log (operator_id, action, target_type, target_id, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (operator_id, action, target_type, target_id, (detail or "")[:500]),
            )
        return True
    except Exception:
        # 审计失败不阻断业务；如配置了日志可在此记录告警
        return False
