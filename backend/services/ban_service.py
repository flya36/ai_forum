# ============================================================
# 用户封禁服务：封禁/解封/状态检查（含惰性解封）
# 数据模型：user.status 复用为封禁标志（0=正常，1=封禁）
#          ban_reason 封禁原因；ban_until 到期时间（NULL=永久）
# 设计要点：
#   - 所有 SQL 参数化
#   - is_user_banned 为"每次请求"调用点，内部做惰性解封（到期自动恢复）
#   - 不允许封禁管理员账号（防自我锁定/提权滥用）
#   - 不允许封禁系统账号（ai_assistant 等，防误封导致 AI 回复/通知链路失效）
# ============================================================
from db import get_cursor

BAN_DURATIONS = {"1": 1, "7": 7, "30": 30}  # 可选天数（永久=days=None）

# 系统账号白名单：禁止封禁
SYSTEM_ACCOUNTS = {"ai_assistant"}


def _is_system_account(user_id):
    """判断是否为系统账号（AI 助手等）。查询失败按非系统账号处理。"""
    if not user_id:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT username FROM `user` WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row and row[0] in SYSTEM_ACCOUNTS)
    except Exception as e:
        print(f"[系统账号判断失败] {e}")
        return False


def is_user_banned(user_id):
    """查询用户是否被封禁，返回 (banned: bool, reason, until)。
    惰性解封：status=1 且 ban_until 已到期 → 自动恢复 status=0 并清空封禁信息。
    未找到用户按未封禁处理（登录等调用方另有校验）。
    系统账号（AI 助手）恒视为未封禁（纵深防御）。"""
    if not user_id:
        return False, None, None
    if _is_system_account(user_id):
        return False, None, None
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT status, ban_reason, ban_until FROM `user` WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False, None, None
            status, reason, until = row[0], row[1], row[2]
            if status != 1:
                return False, None, None
            # 到期惰性解封：下次请求时自动恢复
            if until is not None:
                cursor.execute(
                    "UPDATE `user` SET status = 0, ban_reason = NULL, ban_until = NULL "
                    "WHERE user_id = ? AND status = 1 AND ban_until IS NOT NULL AND ban_until < NOW()",
                    (user_id,),
                )
                if cursor.rowcount:
                    return False, None, None
            return True, reason, str(until) if until else None
    except Exception as e:
        print(f"[封禁状态查询失败] {e}")
        return False, None, None


def ban_user(user_id, reason, days, operator_id=None):
    """封禁用户。reason 必填；days 为 1/7/30（字符串或数字），None/'' 视为永久。
    返回 (success, message)。审计由路由层负责。"""
    reason = (reason or "").strip()
    if not reason:
        return False, "封禁原因不能为空"
    if len(reason) > 255:
        reason = reason[:255]
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT role FROM `user` WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                return False, "用户不存在"
            if row[0] == 1:
                return False, "不能封禁管理员账号"
            if _is_system_account(user_id):
                return False, "系统账号不可封禁"
            if days is None or str(days).strip() == "" or str(days).strip().lower() == "forever":
                ban_until = None  # 永久封禁
            else:
                days_int = int(str(days).strip())
                if days_int not in BAN_DURATIONS.values():
                    return False, "封禁时长不合法"
                cursor.execute(
                    "UPDATE `user` SET status = 1, ban_reason = ?, "
                    "ban_until = DATE_ADD(NOW(), INTERVAL ? DAY) WHERE user_id = ?",
                    (reason, days_int, user_id),
                )
                return True, "封禁成功"
            cursor.execute(
                "UPDATE `user` SET status = 1, ban_reason = ?, ban_until = NULL WHERE user_id = ?",
                (reason, user_id),
            )
        return True, "封禁成功"
    except Exception as e:
        print(f"[封禁用户失败] {e}")
        return False, "封禁失败，请稍后重试"


def unban_user(user_id, operator_id=None):
    """解封用户：恢复 status=0 并清空封禁信息。审计由路由层负责。"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE `user` SET status = 0, ban_reason = NULL, ban_until = NULL "
                "WHERE user_id = ?",
                (user_id,),
            )
            if not cursor.rowcount:
                return False, "用户不存在或未被封禁"
        return True, "解封成功"
    except Exception as e:
        print(f"[解封用户失败] {e}")
        return False, "解封失败，请稍后重试"
