# -*- coding: utf-8 -*-
# ============================================================
# AI 标签推荐服务：基于帖子标题/内容，从现有标签中推荐 3-5 个
# 设计：
#   - 候选标签来自 tags 表（杜绝 AI 幻觉出新标签）
#   - 返回结果严格过滤：不在候选列表中的一律丢弃
#   - 解析失败 / AI 调用失败 / 超时 → 返回 []（降级，不让发帖流程崩溃）
#   - 输入截断（防 prompt 注入与过度消耗 token）
# 安全：本模块不涉及用户输入回显，仅输出候选列表子集
# ============================================================
import json
import urllib.request
import urllib.error

from config import AI_CONFIG
from tag_service import get_all_tags_simple

# 输入截断上限（字符）：标题 + 正文合并不超过该长度后送 AI
MAX_INPUT_LEN = 1200
# 推荐数量范围
MIN_RECOMMEND = 3
MAX_RECOMMEND = 5


def _get_candidates():
    """从 tags 表查询全部现有标签名，返回 (list, set)"""
    try:
        rows = get_all_tags_simple()
    except Exception as e:
        print(f"[AI标签] 候选标签查询失败: {e}")
        return [], set()
    names = [(r["tag_name"] or "").strip() for r in rows if (r["tag_name"] or "").strip()]
    return names, set(names)


def _parse_json_array(text):
    """解析 AI 返回的 JSON 数组；容忍 ```json 代码块包裹。
    非法输入返回 []（不抛异常）。"""
    if not text:
        return []
    s = text.strip()
    # 去掉 ```json ... ``` 包裹
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        s = s.rsplit("```", 1)[0] if "```" in s else s
        s = s.strip()
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        # 容错：从文本中提取 [ ... ] 子串再试一次
        try:
            start, end = s.index("["), s.rindex("]")
            data = json.loads(s[start:end + 1])
        except (ValueError, IndexError):
            return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if isinstance(x, str) and x.strip()]


def recommend_tags(title, content):
    """调用 AI 推荐标签。返回过滤后的标签列表（最多 5 个），失败返回 []。"""
    api_key = (AI_CONFIG.get("api_key") or "").strip()
    base_url = (AI_CONFIG.get("base_url") or "").rstrip("/")
    model = AI_CONFIG.get("model") or ""
    timeout = int(AI_CONFIG.get("timeout", 30) or 30)
    if not api_key or not base_url or not model:
        return []

    candidates, candidate_set = _get_candidates()
    if not candidates:
        return []

    title = (title or "").strip()
    content = (content or "").strip()
    text = (title + "\n" + content).strip()
    if len(text) > MAX_INPUT_LEN:
        text = text[:MAX_INPUT_LEN] + "……"
    if not text:
        return []

    candidate_str = "、".join(candidates)
    prompt = (
        f"请为下面的帖子内容推荐最匹配的 {MIN_RECOMMEND}-{MAX_RECOMMEND} 个标签。\n"
        f"只能从候选标签列表中选择，不能编造列表之外的标签。\n"
        f"候选标签列表：{candidate_str}\n\n"
        f"帖子标题：{title}\n"
        f"帖子内容：{text}\n\n"
        "请只返回一个 JSON 字符串数组，例如 [\"技术\",\"Python\"]，"
        "不要包含任何其他解释、注释或代码块标记。"
    )

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是社区帖子标签推荐助手，只从给定候选标签中选择，输出 JSON 数组。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 200,
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
            answer = result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI标签] AI 调用失败，降级返回空: {e}")
        return []

    raw = _parse_json_array(answer)
    # 过滤：只保留候选列表中的标签，去重，限制数量
    seen, out = set(), []
    for t in raw:
        if t in candidate_set and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= MAX_RECOMMEND:
            break
    return out
