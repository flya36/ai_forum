# ============================================================
# 认证模块：密码哈希、注册、登录验证
# 密码存储格式（新版）：pbkdf2_sha256$迭代次数$盐hex$哈希hex
# 旧版格式 salt$hash（SHA256）仍可校验，用于兼容历史数据
# ============================================================
import hashlib
import hmac
import os
from db import get_cursor

# PBKDF2 迭代次数：SHA256 下 60 万次，显著提高离线暴力破解成本
_PBKDF2_ITERATIONS = 600_000
_ALGO_PREFIX = "pbkdf2_sha256"


def hash_password(password):
    """生成密码哈希：随机16字节盐 + PBKDF2-SHA256，返回 pbkdf2_sha256$迭代$盐$哈希"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_ALGO_PREFIX}${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    """验证密码：兼容新版 pbkdf2 格式与旧版 salt$hash（SHA256）格式"""
    if not stored:
        return False

    if stored.startswith(_ALGO_PREFIX + "$"):
        # 新版 PBKDF2 格式
        try:
            _, iters, salt_hex, pwd_hash = stored.split("$")
            iters = int(iters)
        except (ValueError, TypeError):
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iters
        )
        return hmac.compare_digest(dk.hex(), pwd_hash)

    # 旧版 salt$hash（SHA256）格式
    try:
        salt, pwd_hash = stored.split("$", 1)
    except ValueError:
        return False
    calc = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(calc, pwd_hash)


def register(username, password, nickname=None):
    """
    注册新用户
    返回 (success: bool, message: str)
    """
    if not username or not password:
        return False, "用户名和密码不能为空"
    if len(username) < 2:
        return False, "用户名至少2个字符"
    if len(password) < 6:
        return False, "密码至少6位"

    try:
        with get_cursor() as cursor:
            # 1. 检查用户名是否已存在
            cursor.execute("SELECT user_id FROM `user` WHERE username = ?", (username,))
            if cursor.fetchone():
                return False, "用户名已存在"

            # 2. 密码哈希后写入数据库，默认 role=0 普通用户
            pwd_stored = hash_password(password)
            cursor.execute(
                "INSERT INTO `user` (username, password_hash, nickname, role, status) "
                "VALUES (?, ?, ?, 0, 0)",
                (username, pwd_stored, nickname or username),
            )
        return True, "注册成功"
    except Exception as e:
        print(f"[注册失败] {e}")
        return False, "注册失败，请稍后重试"


def change_password(user_id, old_password, new_password):
    """
    修改密码：校验旧密码 → 校验新密码（≥8位、与旧密码不同）→ PBKDF2 重哈希入库
    安全提示统一：旧密码错误 / 新密码过短 / 新旧相同 均返回同一文案，避免泄露具体失败原因。
    返回 (success: bool, message: str)
    """
    _UNIFIED_MSG = "密码修改失败，请检查输入"
    if not old_password or not new_password:
        return False, _UNIFIED_MSG
    if len(new_password) < 8:
        return False, _UNIFIED_MSG
    # 密码策略：必须同时包含字母与数字（统一提示，不泄露具体失败原因）
    if not (any(c.isalpha() for c in new_password) and any(c.isdigit() for c in new_password)):
        return False, _UNIFIED_MSG
    if old_password == new_password:
        return False, _UNIFIED_MSG
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT password_hash FROM `user` WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row or not row[0]:
                return False, _UNIFIED_MSG
            stored = row[0]
            if not verify_password(old_password, stored):
                return False, _UNIFIED_MSG
            if verify_password(new_password, stored):
                # 新旧密码相同（对新旧两种哈希格式均能正确判断）
                return False, _UNIFIED_MSG
            cursor.execute(
                "UPDATE `user` SET password_hash = ? WHERE user_id = ?",
                (hash_password(new_password), user_id),
            )
        return True, "密码修改成功"
    except Exception as e:
        print(f"[修改密码失败] {e}")
        return False, "密码修改失败，请稍后重试"


def login(username, password):
    """
    登录验证
    返回 (success: bool, message: str, user_info: dict | None)
    """
    if not username or not password:
        return False, "用户名和密码不能为空", None

    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id, username, password_hash, nickname, role, status, "
                "ban_reason, ban_until "
                "FROM `user` WHERE username = ?",
                (username,),
            )
            row = cursor.fetchone()
            if not row:
                # 统一提示，避免用户名枚举（"不存在"与"密码错"使用同一文案）
                return False, "用户名或密码错误", None

            # pyodbc 按索引取值
            user_id = row[0]
            uname = row[1]
            password_hash = row[2]
            nickname = row[3]
            role = row[4]
            status = row[5]
            ban_reason = row[6]
            ban_until = row[7]

            if status != 0:
                # 到期惰性解封：下次请求/登录时自动恢复（与 ban_service.is_user_banned 同语义）
                if ban_until is not None:
                    cursor.execute(
                        "UPDATE `user` SET status = 0, ban_reason = NULL, ban_until = NULL "
                        "WHERE user_id = ? AND status = 1 AND ban_until IS NOT NULL AND ban_until < NOW()",
                        (user_id,),
                    )
                    if cursor.rowcount:
                        status = 0  # 已到期自动解封，继续走正常登录流程
                if status != 0:
                    # 登录被拒：记录审计（operator_id=0 表示系统/未登录主体），
                    # 提示包含封禁原因与解封时间（对被禁用户本人可见，属正常告知）
                    try:
                        from audit import log_audit
                        log_audit(
                            0, "login_blocked", "user", user_id,
                            detail=f"username={uname};reason={ban_reason or ''};until={ban_until or '永久'}",
                        )
                    except Exception:
                        pass
                    if ban_until is not None:
                        return False, (
                            "账号已被封禁，如有疑问请联系管理员"
                            + (f"（原因：{ban_reason}，解封时间：{ban_until}）" if ban_reason else f"（解封时间：{ban_until}）")
                        ), None
                    return False, (
                        "账号已被封禁，如有疑问请联系管理员"
                        + (f"（原因：{ban_reason}，永久封禁）" if ban_reason else "（永久封禁）")
                    ), None

            if not verify_password(password, password_hash):
                return False, "用户名或密码错误", None

        user_info = {
            "user_id": user_id,
            "username": uname,
            "nickname": nickname,
            "role": role,  # 0=普通用户，1=管理员
        }
        return True, "登录成功", user_info
    except Exception as e:
        print(f"[登录失败] {e}")
        return False, "登录失败，请稍后重试", None
