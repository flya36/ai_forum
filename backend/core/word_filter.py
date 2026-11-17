# -*- coding: utf-8 -*-
# ============================================================
# 敏感词过滤模块（内存加载 + 变体识别 + 防 ReDoS）
#
# 设计要点：
#   - 词库文件 backend/config/sensitive_words.txt（每行一词，# 开头为注释）
#   - 应用启动/首次调用时加载到内存并编译，不阻塞启动：
#       * 文件不存在 / 为空 / 编译失败 → 过滤停用并告警，应用照常运行
#       * 词数超过 MAX_WORDS 时截断并告警（防超长编译阻塞）
#   - 匹配规则：
#       * 大小写不敏感（re.IGNORECASE + lower）
#       * 全角/半角兼容（NFKC 归一化，如 "Ａ" → "A"、"ｆｕｃｋ" → "fuck"）
#       * 变体识别：词内字符间允许 0~3 个分隔符（空格/*/-/_/.等），
#         如 "敏 * 感 * 词" 命中 "敏感词"
#   - ReDoS 防护：每个词构建为"字面量字符 + 有界量词{0,3} + 字面量字符"的
#     线性正则（无嵌套量词、无贪婪回溯），合并用 | 交替，整体线性复杂度。
#   - 词库文件位于 backend 目录（非静态服务范围），普通用户无法通过 URL 读取。
# ============================================================
import os
import re
import unicodedata

_WORD_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "sensitive_words.txt")

MAX_WORDS = 2000          # 词库上限（防超长编译阻塞启动）
MAX_WORD_LEN = 30         # 单词长度上限（超长词跳过）
_SEP = r"[\s*\-_.·、,，]{0,3}"   # 变体分隔符：0~3 个空白/符号

_words = []               # 已加载词（有序）
_combined = None          # 合并编译后的正则（None = 未启用）
_STRIP = re.compile(r"[\s*\-_.·、,，]+")   # 用于还原命中的变体片段


def normalize(text):
    """归一化：全角→半角（NFKC）+ 统一小写，用于匹配前处理"""
    return unicodedata.normalize("NFKC", text or "").lower()


def _build_pattern(word):
    """单个词构建为字面量字符 + 有界分隔量词，避免灾难性回溯"""
    return _SEP.join(re.escape(ch) for ch in word)


def load_sensitive_words():
    """加载词库到内存并编译。任何失败都降级为"过滤停用"，不抛异常、不阻塞启动。"""
    global _words, _combined
    words = []
    try:
        with open(_WORD_FILE, encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if not w or w.startswith("#"):
                    continue
                if len(w) > MAX_WORD_LEN:
                    continue
                words.append(w)
    except FileNotFoundError:
        print("[敏感词] 词库文件不存在，敏感词过滤停用")
        _words, _combined = [], None
        return
    except OSError as e:
        print("[敏感词] 读取词库失败：%s，过滤停用" % e)
        _words, _combined = [], None
        return

    if len(words) > MAX_WORDS:
        print("[敏感词] 词库 %d 条超过上限 %d，已截断" % (len(words), MAX_WORDS))
        words = words[:MAX_WORDS]
    if not words:
        _words, _combined = [], None
        return

    try:
        pattern = "|".join(_build_pattern(w) for w in words)
        _combined = re.compile(pattern, re.IGNORECASE)
        _words = words
        print("[敏感词] 已加载 %d 条（变体识别开启，防 ReDoS）" % len(words))
    except re.error as e:
        print("[敏感词] 正则编译失败，过滤停用：%s" % e)
        _words, _combined = [], None


def check_text(text):
    """检测文本是否含敏感词（含变体）。
    返回命中词列表（去重、保序）；空列表 = 通过。"""
    if not text:
        return []
    if not _combined:
        return []
    t = normalize(text)
    hits = []
    for m in _combined.finditer(t):
        # 正则命中的是"含分隔符的变体片段"，去分隔符还原后与词库比对
        frag = _STRIP.sub("", m.group(0))
        for w in _words:
            if w in frag and w not in hits:
                hits.append(w)
                break
    return hits


def is_blocked(text):
    """便捷判断：含敏感词返回 True（供路由/Service 调用）"""
    return bool(check_text(text))


def block_text(text, operator_id, target_type, target_id=None):
    """组合拦截：检查敏感词 → 命中则落库审计并返回 (True, 提示)。
    供 Service 层在参数化 SQL 之前调用；未命中返回 (False, "")。"""
    hits = check_text(text)
    if not hits:
        return False, ""
    try:
        from audit import log_audit
        log_audit(
            operator_id or 0, "sensitive_word_block", target_type, target_id,
            detail="hits=%s;text=%s" % (",".join(hits), (text or "")[:50]),
        )
    except Exception:
        pass
    return True, "内容包含敏感词：%s，请修改后再发布" % "、".join(hits)


def reload():
    """管理脚本用：热重载词库（增删词后调用）"""
    load_sensitive_words()


# 模块导入即加载（应用启动时执行一次；失败不抛异常）
load_sensitive_words()
