# -*- coding: utf-8 -*-
# ============================================================
# 全局配置服务（app_settings 表）：键值对存取
# 目前用于：AI 智能回复开关（ai_reply_enabled）
# 安全：所有 SQL 参数化；键白名单由调用方控制
# ============================================================
from db import get_cursor

# 已知配置键（白名单，防任意键写入）
KNOWN_KEYS = ("ai_reply_enabled", "ai_reply_daily_limit")


def get_setting(key, default=""):
    if key not in KNOWN_KEYS:
        return default
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT svalue FROM app_settings WHERE skey = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    except Exception as e:
        print(f"[配置读取失败] {key}: {e}")
        return default


def set_setting(key, value):
    if key not in KNOWN_KEYS:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_settings (skey, svalue) VALUES (?, ?) "
                "ON DUPLICATE KEY UPDATE svalue = VALUES(svalue)",
                (key, str(value)[:255]))
        return True
    except Exception as e:
        print(f"[配置写入失败] {key}: {e}")
        return False


def ai_reply_enabled():
    """AI 智能回复全局开关（默认开启）"""
    return get_setting("ai_reply_enabled", "1") == "1"
