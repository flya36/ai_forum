# ============================================================
# 数据库连接工具：封装 pyodbc 连接，其他模块通过 get_connection() 获取连接
# ============================================================

import pyodbc
from contextlib import contextmanager
from config import DB_CONFIG


def get_connection():
    """获取一个新的数据库连接，用完记得 close()。
    安全要求：
      - 使用最小权限应用账号（见 config.py，严禁 root）
      - 连接字符串来自配置常量，不接收任何用户输入
      - 连接超时 10 秒，防止异常场景挂起"""
    conn_str = (
        f"DRIVER={{{DB_CONFIG['driver']}}};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        f"USER={DB_CONFIG['user']};"
        f"PASSWORD={DB_CONFIG['password']};"
        f"CHARSET={DB_CONFIG['charset']};"
    )
    return pyodbc.connect(conn_str, timeout=10)


@contextmanager
def get_cursor():
    """上下文管理器：统一管理连接与游标的生命周期。

    正常退出时自动 commit，异常时自动 rollback 并重新抛出。
    用法：
        with get_cursor() as cursor:
            cursor.execute(...)
            rows = cursor.fetchall()
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        cursor.close()
        conn.close()
