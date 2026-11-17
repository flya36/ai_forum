# -*- coding: utf-8 -*-
# ============================================================
# AI 智能回复调度器
# 触发机制：后台定时任务每 5 分钟扫描一次（app.py 启动时 gevent.spawn 循环）
# 扫描条件：
#   - 帖子创建 > 10 分钟
#   - 评论数 = 0
#   - ai_replied = 0（未触发过）
#   - 状态正常（未删除）且非私密帖（私密内容不外泄给 AI）
#   - 每次最多处理 3 条，串行调用 AI（sleep 控制 RPM，防 429）
# 产出：以 ai_assistant 身份写评论 + 标记 ai_replied + 通知作者（落库 + socket 实时）
# 降级：AI 失败/超时/返回空 → 记录日志并跳过（不标记，下次重试）
# 开关：settings_service.ai_reply_enabled()=False 时任务立即停止
# 安全：所有 SQL 参数化；ai_generator 可注入（测试用 mock）
# ============================================================
import json
import secrets
import time
import urllib.request
import urllib.error

from config import AI_CONFIG
from db import get_cursor
from settings_service import ai_reply_enabled, get_setting
from auth import hash_password
from comment_service import add_comment
from notification_service import add_notification
from markdown_render import render_markdown
from socketio_bus import socketio
from word_filter import block_text

AI_ASSISTANT_USERNAME = "ai_assistant"
AI_ASSISTANT_NICKNAME = "AI 助手"
AI_ASSISTANT_SIGNATURE = "我是论坛的 AI 助手，随时解答问题。"
MIN_AGE_MINUTES = 10          # 发帖满 10 分钟无真人回复才触发
MAX_PER_RUN = 3               # 单次任务最多处理 3 条
AI_SLEEP_SECONDS = 2          # 串行调用间隔（千问免费版 RPM 限制）
REPLY_MAX_LEN = 150           # AI 回复字数上限
INPUT_MAX_LEN = 2000          # 送 AI 的标题+正文截断

# 每日回复上限：进程内计数（单进程 gevent 适用），跨日自动重置
_daily_count = 0
_daily_date = None


def ensure_ai_assistant():
    """确保 AI 助手系统账号存在（密码随机不可登录，角色普通用户）。
    返回 ai_assistant 的 user_id；失败返回 None。"""
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM `user` WHERE username = ?",
                (AI_ASSISTANT_USERNAME,))
            row = cursor.fetchone()
            if row:
                return row[0]
        pwd_hash = hash_password("sys_" + secrets.token_hex(16))
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO `user` (username, password_hash, nickname, signature, role, status) "
                "VALUES (?, ?, ?, ?, 0, 0)",
                (AI_ASSISTANT_USERNAME, pwd_hash,
                 AI_ASSISTANT_NICKNAME, AI_ASSISTANT_SIGNATURE))
            new_id = getattr(cursor, "lastrowid", None)
            return new_id or None
    except Exception as e:
        print(f"[AI助手] 创建系统账号失败: {e}")
        return None


def _generate_reply(title, content):
    """调用 AI 生成建设性回复（50-150 字）。失败/超时/返回空 → None（下次重试）。"""
    api_key = (AI_CONFIG.get("api_key") or "").strip()
    base_url = (AI_CONFIG.get("base_url") or "").rstrip("/")
    model = AI_CONFIG.get("model") or ""
    timeout = int(AI_CONFIG.get("timeout", 30) or 30)
    if not api_key or not base_url or not model:
        return None
    text = ((title or "") + "\n" + (content or "")).strip()
    if len(text) > INPUT_MAX_LEN:
        text = text[:INPUT_MAX_LEN] + "……"
    prompt = (
        "你是一个专业的AI论坛助手。请针对以下帖子给出友好、专业、有价值的回复。要求：\n"
        "1. 态度礼貌，语气自然；\n"
        "2. 字数控制在 50-150 字；\n"
        "3. 提供建设性意见或补充相关信息；\n"
        "4. 不要简单回复“不错”“顶”之类的无意义内容。\n\n"
        f"帖子标题：{title}\n"
        f"帖子内容：{text}\n\n"
        "请直接输出回复内容，不要添加任何前缀或解释。"
    )
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是社区 AI 助手，回复友好专业、简洁有价值。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 300,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            answer = (result["choices"][0]["message"]["content"] or "").strip()
            return answer[:REPLY_MAX_LEN] if answer else None
    except Exception as e:
        print(f"[AI助手] AI 回复调用失败（跳过，下次重试）: {e}")
        return None


def _find_candidates(limit=MAX_PER_RUN, only_post_id=None):
    """查找符合条件的帖子（参数化 SQL）。
    跳过：置顶帖/公告帖（is_top=1，公告以置顶表达）、私密帖、已回复、有评论。
    only_post_id：测试专用，只处理指定帖子（防止测试钩子污染真实帖子）。"""
    sql = """
        SELECT p.post_id, p.title, p.content, p.author_id
        FROM post p
        WHERE p.ai_replied = 0
          AND p.is_private = 0
          AND p.is_top = 0
          AND p.create_time < DATE_SUB(NOW(), INTERVAL ? MINUTE)
          AND (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) = 0
    """
    params = [MIN_AGE_MINUTES]
    if only_post_id:
        sql += " AND p.post_id = ?"
        params.append(only_post_id)
    sql += " ORDER BY p.create_time ASC LIMIT ?"
    params.append(limit)
    with get_cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _daily_limit_reached():
    """每日最大回复数检查（进程内当日计数；上限存 app_settings 可调）"""
    global _daily_count, _daily_date
    import datetime
    today = datetime.date.today().isoformat()
    if _daily_date != today:
        _daily_date = today
        _daily_count = 0
    try:
        limit = int(get_setting("ai_reply_daily_limit", "50") or "50")
    except (TypeError, ValueError):
        limit = 50
    if limit <= 0:
        return True  # 0/负 = 禁用
    if _daily_count >= limit:
        print(f"[AI助手] 今日 AI 回复已达上限 {limit} 条，本次任务跳过")
        return True
    return False


def _mark_replied(post_id):
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE post SET ai_replied = 1 WHERE post_id = ?", (post_id,))


def _broadcast_ai_comment(post_id, ai_uid, reply, author_id):
    """SocketIO 实时推送：帖子房间 new_comment + 作者房间通知未读角标。"""
    try:
        from comment_service import get_comments_by_post
        comments = get_comments_by_post(post_id)
        c = comments[-1] if comments else None
        if c:
            socketio.emit("new_comment", {
                "comment_id": c["comment_id"],
                "user_id": c["user_id"],
                "nickname": c["nickname"],
                "avatar_path": c["avatar_path"],
                "content": c["content"],
                "content_html": render_markdown(c["content"]),
                "create_time": c["create_time"][:16],
                "floor": c["floor"],
            }, room=f"post_{post_id}")
    except Exception as e:
        print(f"[AI助手] 评论广播失败: {e}")
    # 作者未读角标实时 +1
    try:
        from notification_service import get_unread_count
        unread = get_unread_count(author_id)
        socketio.emit("notification", {
            "type": "comment", "content": "AI 助手 评论了你的帖子",
            "unread": unread,
        }, room=f"user_{author_id}")
    except Exception as e:
        print(f"[AI助手] 通知推送失败: {e}")


def scan_and_reply(ai_generator=None, only_post_id=None):
    """扫描并执行 AI 回复。返回处理条数。
    ai_generator(title, content) -> str|None 可注入（测试 mock；默认真实 AI）。
    only_post_id：测试专用，只处理指定帖子。"""
    global _daily_count
    if not ai_reply_enabled():
        print("[AI助手] 全局开关已关闭，本次任务跳过")
        return 0
    if _daily_limit_reached():
        return 0
    ai_uid = ensure_ai_assistant()
    if not ai_uid:
        print("[AI助手] 系统账号不可用，跳过本次任务")
        return 0
    generator = ai_generator or _generate_reply
    rows = _find_candidates(only_post_id=only_post_id)
    count = 0
    for r in rows:
        post_id, title, content, author_id = r[0], r[1], r[2], r[3]
        try:
            reply = generator(title or "", content or "")
            if not reply:
                print(f"[AI助手] 帖子 {post_id} 未生成回复（跳过，不标记，下次重试）")
                continue
            # 敏感词过滤：AI 生成内容也过词库（命中则跳过，不标记）
            blocked, _ = block_text(reply, ai_uid, "comment", post_id)
            if blocked:
                print(f"[AI助手] 帖子 {post_id} 生成内容命中敏感词，跳过")
                continue
            success, _, new_cid = add_comment(post_id, ai_uid, reply)
            if not success:
                print(f"[AI助手] 帖子 {post_id} 评论写入失败，跳过")
                continue
            _mark_replied(post_id)
            # 通知作者（落库 + 实时推送）；携带 comment_id 便于精确关联/撤回
            content_short = reply[:30]
            add_notification(
                author_id, "comment", post_id, ai_uid,
                f"AI 助手 评论了你的帖子：{content_short}",
                comment_id=new_cid)
            _broadcast_ai_comment(post_id, ai_uid, reply, author_id)
            count += 1
            _daily_count += 1  # 计入每日上限
            print(f"[AI助手] 已回复帖子 {post_id}（作者 {author_id}），今日累计 {_daily_count}")
            time.sleep(AI_SLEEP_SECONDS)  # RPM 控制
        except Exception as e:
            print(f"[AI助手] 帖子 {post_id} 处理异常（跳过，下次重试）: {e}")
            continue
    return count
