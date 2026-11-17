# ============================================================
# RAG 智能问答服务：文档分块、TF-IDF索引、语义检索、回答生成
# 纯本地实现，无需外部API或大型依赖
# ============================================================
import re
import json
import math
import threading
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from db import get_cursor
from config import AI_CONFIG

# 中文停用词（常见无意义字符）
STOP_WORDS = set("的了是在我有和就不人都一上也很到说要去你会着没有看好自己这那他她它们什么怎么为什么哪吗呢吧啊呀哦嗯的话还是然后因为所以如果但是虽然而且并且或者以及等等")

# 索引缓存（全局单例，首次使用时构建）
_index_cache = None
_index_docs = None
_index_lock = threading.Lock()


# ============================================================
# 分词
# ============================================================
def tokenize(text):
    """简单中文分词：英文/数字直接作为token，中文用字符级unigram+bigram"""
    if not text:
        return []
    # 去除标点和特殊字符，保留中英文和数字
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', ' ', text)
    tokens = []
    for part in text.split():
        if re.match(r'^[a-zA-Z0-9]+$', part):
            tokens.append(part.lower())
        else:
            chars = list(part)
            tokens.extend(chars)  # unigram
            for i in range(len(chars) - 1):
                tokens.append(chars[i] + chars[i + 1])  # bigram
    # 过滤停用词
    return [t for t in tokens if t not in STOP_WORDS]


# ============================================================
# 索引构建
# ============================================================
def build_index():
    """从数据库加载所有知识块，构建TF-IDF索引，返回知识块数量"""
    global _index_cache, _index_docs

    with get_cursor() as cursor:
        cursor.execute("""
            SELECT kc.chunk_id, kc.post_id, kc.chunk_content, p.title
            FROM knowledge_chunk kc
            JOIN post p ON kc.post_id = p.post_id
            WHERE p.is_private = 0
            ORDER BY kc.chunk_id
        """)
        rows = cursor.fetchall()

    _index_docs = []
    doc_tokens_list = []

    for r in rows:
        chunk_id, post_id, content, title = r[0], r[1], r[2], r[3]
        full_text = f"{title} {content}"
        tokens = tokenize(full_text)
        _index_docs.append({
            "chunk_id": chunk_id,
            "post_id": post_id,
            "title": title,
            "content": content,
        })
        doc_tokens_list.append(tokens)

    # 计算IDF
    N = len(doc_tokens_list)
    df = defaultdict(int)
    for tokens in doc_tokens_list:
        for term in set(tokens):
            df[term] += 1

    idf = {}
    for term, freq in df.items():
        idf[term] = math.log((N + 1) / (freq + 1)) + 1

    # 计算每个文档的TF-IDF向量
    doc_vectors = []
    for tokens in doc_tokens_list:
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        vec = {}
        for term, count in tf.items():
            vec[term] = (count / total) * idf.get(term, 0)
        doc_vectors.append(vec)

    _index_cache = {"idf": idf, "doc_vectors": doc_vectors, "N": N}
    return len(_index_docs)


def ensure_index():
    """确保索引已构建（懒加载），带锁防止并发构建出半成品索引"""
    if _index_cache is None:
        with _index_lock:
            if _index_cache is None:
                build_index()


def invalidate_index():
    """清除索引缓存（帖子增删后调用，下次检索时自动重建）"""
    global _index_cache, _index_docs
    with _index_lock:
        _index_cache = None
        _index_docs = None


# ============================================================
# 检索
# ============================================================
def search(query, top_k=3):
    """根据查询检索相关知识块，返回按相关度排序的结果列表"""
    ensure_index()

    if _index_cache["N"] == 0:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # 计算查询的TF-IDF向量
    qtf = Counter(query_tokens)
    qtotal = len(query_tokens)
    qvec = {}
    for term, count in qtf.items():
        qvec[term] = (count / qtotal) * _index_cache["idf"].get(term, 0)

    # 计算余弦相似度 + 关键词匹配加分
    scores = []
    for i, dvec in enumerate(_index_cache["doc_vectors"]):
        dot = sum(qvec.get(term, 0) * dvec.get(term, 0) for term in qvec)
        qnorm = math.sqrt(sum(v ** 2 for v in qvec.values()))
        dnorm = math.sqrt(sum(v ** 2 for v in dvec.values()))
        sim = dot / (qnorm * dnorm) if qnorm > 0 and dnorm > 0 else 0

        # 关键词直接命中加分
        keyword_bonus = sum(0.03 for term in set(query_tokens) if term in dvec)
        scores.append((i, sim + keyword_bonus))

    scores.sort(key=lambda x: x[1], reverse=True)

    # 去重（同一帖子只取最相关的一块），取top_k
    results = []
    seen_posts = set()
    for i, score in scores:
        doc = _index_docs[i]
        if doc["post_id"] in seen_posts:
            continue
        seen_posts.add(doc["post_id"])
        results.append({
            "chunk_id": doc["chunk_id"],
            "post_id": doc["post_id"],
            "title": doc["title"],
            "content": doc["content"],
            "score": round(score, 4),
        })
        if len(results) >= top_k:
            break

    return results


# ============================================================
# 回答生成
# ============================================================
def generate_answer(query):
    """基于检索结果生成回答，返回 {answer, sources}"""
    results = search(query, top_k=5)
    # 过滤掉相关度为0的结果（完全不相关）
    results = [r for r in results if r["score"] > 0]

    if not results or results[0]["score"] < 0.05:
        return {
            "answer": (
                "暂未在社区知识库中找到与您问题相关的内容。\n\n"
                "建议您：\n"
                "1. 尝试换个关键词重新提问\n"
                "2. 在社区中发布帖子，向其他用户求助\n"
                "3. 浏览相关板块的帖子"
            ),
            "sources": [],
        }

    # 拼接检索到的上下文
    context_parts = []
    for i, r in enumerate(results, 1):
        context_parts.append(f"【帖子{i}】《{r['title']}》\n{r['content'][:500]}")
    context = "\n\n".join(context_parts)

    # 调用AI生成总结
    ai_answer = _ai_chat(query, context)
    if ai_answer:
        return {"answer": ai_answer, "sources": results}

    # AI调用失败，降级为拼接
    parts = ["根据社区知识库中的相关内容，为您整理如下：\n"]
    for i, r in enumerate(results, 1):
        preview = r["content"][:250]
        if len(r["content"]) > 250:
            preview += "..."
        parts.append(f"【参考{i}】《{r['title']}》\n{preview}\n")
    parts.append(f"\n以上信息来源于社区中的 {len(results)} 篇帖子。")
    return {"answer": "\n".join(parts), "sources": results}


def _ai_chat(query, context):
    """调用AI接口，基于检索到的上下文回答用户问题"""
    api_key = AI_CONFIG.get("api_key", "").strip()
    if not api_key:
        return None

    base_url = AI_CONFIG.get("base_url", "").rstrip("/")
    model = AI_CONFIG.get("model", "")
    timeout = AI_CONFIG.get("timeout", 60)

    if not base_url or not model:
        return None

    # 截断过长上下文
    if len(context) > 3000:
        context = context[:3000] + "..."

    prompt = (
        f"用户提问：{query}\n\n"
        f"以下是社区中检索到的相关帖子内容：\n{context}\n\n"
        "请基于以上帖子内容，用自己的话回答用户的问题。要求：\n"
        "1. 综合多篇帖子的信息，给出有条理的总结回答\n"
        "2. 必须严格基于帖子内容，不要编造帖子中没有的信息\n"
        "3. 语言通顺，分点或分段清晰表达\n"
        "4. 如果帖子内容不足以回答问题，就说明根据现有信息无法完整回答"
    )

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个社区智能问答助手，根据检索到的帖子内容回答用户问题，语言简洁有条理。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 500,
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
            if answer:
                return answer
    except Exception as e:
        print(f"[AI问答] 调用失败：{e}")
    return None


# ============================================================
# 文档分块
# ============================================================
def chunk_post(post_id, title, content):
    """将单个帖子分块并存入knowledge_chunk表，返回分块数量"""
    with get_cursor() as cursor:
        # 清除该帖子旧的分块
        cursor.execute("DELETE FROM knowledge_chunk WHERE post_id = ?", (post_id,))

        # 分块策略：按句号/换行切分段落，每块约300字
        full_text = f"{title}。{content}"
        paragraphs = re.split(r'[\n。！？；]', full_text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) > 300 and current:
                chunks.append(current)
                current = para
            else:
                current = current + "。" + para if current else para
        if current:
            chunks.append(current)

        if not chunks:
            chunks = [full_text[:500]]

        for idx, chunk_content in enumerate(chunks):
            cursor.execute(
                "INSERT INTO knowledge_chunk (post_id, chunk_content, chunk_index) VALUES (?, ?, ?)",
                (post_id, chunk_content, idx),
            )

        return len(chunks)
