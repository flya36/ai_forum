# ============================================================
# @提及 服务：从发帖/评论/编辑内容中检测 @用户名 并发送站内通知
# 安全约束：
#   - 所有用户名查询均为参数化 SQL（? 占位），禁止字符串拼接，防注入；
#   - @提及 目标限定为本系统已注册用户，不解析邮箱/外部链接/URL；
#   - 用户自己提及自己不通知；@不存在/内容为空自动跳过；
#   - 单篇内容最多处理 MENTION_LIMIT 个提及，超出静默丢弃（防 DoS）；
#   - 同一内容同一用户只通知一次（有序去重）；编辑场景可跳过已通知用户。
# ============================================================
import re

from db import get_cursor
from notification_service import add_notification

# 单篇帖子/评论最多处理多少个提及，超出静默丢弃（防刷屏/DoS）
MENTION_LIMIT = 10

# 用户名规则与注册校验一致：字母、数字、下划线、中文，长度 2~30。
# 排除单字符（@a、@_ 等）与分隔符后缀，避免把邮箱/路径误当提及。
_MENTION_RE = re.compile(r"@([\w\u4e00-\u9fa5][\w\u4e00-\u9fa5_-]{1,29})")


def extract_mentioned_usernames(content):
    """从文本中提取 @用户名，有序去重（保留首次出现顺序，结果确定性强）。
    内容为空或无非重复命中返回空列表。"""
    if not content:
        return []
    return list(dict.fromkeys(_MENTION_RE.findall(content)))


def resolve_mention_targets(usernames, exclude_user_id=None):
    """按用户名逐条参数化查询用户（避免 IN 拼接），跳过 exclude_user_id（自己）。
    返回 [{"user_id": int, "nickname": str}]，仅包含真实存在且未去重过的用户。"""
    if not usernames:
        return []
    targets = []
    seen_ids = set()
    with get_cursor() as cursor:
        for name in usernames:
            cursor.execute(
                "SELECT user_id, nickname FROM `user` WHERE username = ?",
                (name,),
            )
            row = cursor.fetchone()
            if row and row[0] != exclude_user_id and row[0] not in seen_ids:
                seen_ids.add(row[0])
                targets.append({"user_id": row[0], "nickname": row[1]})
    return targets


def get_already_mentioned_user_ids(post_id):
    """查询某帖子已发送过 mention 通知的用户 ID 集合（编辑场景去重用）"""
    if not post_id:
        return set()
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT user_id FROM notification WHERE post_id = ? AND type = 'mention'",
            (post_id,),
        )
        return {row[0] for row in cursor.fetchall()}


def notify_mentions(content, post_id, from_user_id, from_nickname, action_desc,
                    skip_already_notified=False, comment_id=None):
    """检测内容中的 @提及 并发送站内通知（notify_type="mention"）。
    - 有序去重：同一内容对同一用户只通知一次；
    - 上限 MENTION_LIMIT 个，超出静默丢弃；
    - skip_already_notified=True（编辑场景）：跳过该帖子已通知过的用户，避免重复打扰。
    - comment_id：评论场景传入，前端可跳转 /post/<post_id>#comment-<comment_id>。
    返回被通知列表 [(user_id, message), ...]，供路由层做实时推送。
    action_desc 示例："发布的帖子" / "评论的帖子" / "编辑的帖子"。"""
    if not content:
        return []
    names = extract_mentioned_usernames(content)
    if not names:
        return []
    names = names[:MENTION_LIMIT]
    targets = resolve_mention_targets(names, exclude_user_id=from_user_id)
    if not targets:
        return []
    if skip_already_notified and post_id:
        already = get_already_mentioned_user_ids(post_id)
        targets = [t for t in targets if t["user_id"] not in already]
        if not targets:
            return []
    # 拉黑限制：被提及者拉黑了提者 → 静默跳过（B 不能 @ 拉黑者）
    from follow_service import is_blocked as _is_blocked_by
    targets = [t for t in targets if not _is_blocked_by(t["user_id"], from_user_id)]
    if not targets:
        return []
    preview = " ".join((content or "").strip().split())[:30]
    notified = []
    for t in targets:
        msg = "%s 在%s中提到了你：%s" % (from_nickname, action_desc, preview)
        add_notification(
            user_id=t["user_id"], notify_type="mention",
            post_id=post_id, from_user_id=from_user_id, content=msg,
            comment_id=comment_id,
        )
        notified.append((t["user_id"], msg))
    return notified
