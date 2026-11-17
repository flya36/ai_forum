# ============================================================
# 配置：凭据一律来自环境变量或本地 backend/config/.env（已 gitignore）
# 本文件禁止出现任何真实密钥；缺失时启动即报错，不再静默使用兜底口令
# ============================================================
import os
from pathlib import Path

_ENV_FILE = Path(__file__).with_name(".env")


def _load_env_file():
    """读取同目录 .env（KEY=VALUE 逐行），已存在的环境变量优先，不被文件覆盖。"""
    if not _ENV_FILE.exists():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _require(name, hint):
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            "缺少必填配置 %s。\n说明：%s\n最简单做法：编辑 %s 填入后重启应用。" % (name, hint, _ENV_FILE)
        )
    return val


_load_env_file()

DB_CONFIG = {
    "driver": os.environ.get("DB_DRIVER", "MySQL ODBC 9.7 Unicode Driver"),
    "server": os.environ.get("DB_SERVER", "localhost"),
    "database": os.environ.get("DB_NAME", "ai_forum"),
    "user": os.environ.get("DB_USER", "root"),
    "password": _require("DB_PASSWORD", "MySQL 账号口令，按你实际设置的填写"),
    "charset": "utf8mb4",
}

# AI 接口：Key 允许为空，为空时应用自动降级为本地检索拼接回答
AI_CONFIG = {
    "api_key": os.environ.get("AI_API_KEY", "").strip(),
    "base_url": os.environ.get("AI_BASE_URL", "https://api.siliconflow.cn/v1"),
    "model": os.environ.get("AI_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
    "timeout": int(os.environ.get("AI_TIMEOUT", "30")),
}

# 会话签名密钥：必填，缺失即报错
# 生成方式：python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = _require("FLASK_SECRET_KEY", "会话签名密钥，用于登录态签名")
