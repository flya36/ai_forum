# -*- coding: utf-8 -*-
# ============================================================
# 帖子语义搜索服务：本地向量索引 + 本地 embedding
#   - embedding 优先使用 sentence-transformers 本地模型
#     （paraphrase-multilingual-MiniLM-L12-v2，支持中文，零 API 消耗）
#   - 本地模型不可用时降级 OpenAI 兼容 embedding API，每次调用 sleep 7s 控频
#   - 向量存储优先 ChromaDB；Windows App Control 阻止 grpcio DLL 等环境
#     下自动降级内置 numpy 向量存储（同样持久化到 backend/vector_store/）
#   - 向量库不可用/未初始化时，由路由层降级为 SQL LIKE 搜索
# 隐私边界：仅索引公开帖（is_private=0）；搜索回源时再校验作者未被封禁/激活
# ============================================================
import os
import sys
import threading
import time

from filelock import FileLock, Timeout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "config"))

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from db import get_cursor  # noqa: E402

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_DIR = os.path.join(os.path.dirname(__file__), "..", "vector_store")
REBUILD_LOCK_PATH = os.path.join(VECTOR_DIR, ".rebuild.lock")
EMBED_API_SLEEP = 7.0   # 降级 API 每次调用间隔（秒），防 RPM 打爆
BATCH_SIZE = 50         # 初始批量索引分批大小（每批后 sleep 0.5s）
SIM_THRESHOLD = 0.25    # 相似度阈值（MiniLM 多语言模型对中文短查询余弦值整体偏低，
                        # 实测相关帖约 0.26~0.34，0.5 会误杀全部；0.25 保留相关帖并滤除无关帖）
DISABLED = False        # 测试钩子可置 True 模拟向量库不可用（降级 LIKE）

_model = None
_model_loading = False
_rebuild_lock = threading.Lock()
_write_lock = threading.RLock()  # 可重入：串行化向量存储写入（_store_update 嵌套 _store_add），防并发写损坏

# ========== embedding ==========
def _get_model():
    """懒加载本地 embedding 模型（仅用本地缓存，不联网）。失败返回 None。
    并发保护：预热协程加载期间，其他调用方等待其完成，避免重复加载 11s+ 阻塞。"""
    global _model, _model_loading
    if _model is not None:
        return _model if _model is not False else None
    if _model_loading:
        # 预热协程正在加载：等待其完成（gevent 环境 sleep 让出，不阻塞其他请求）
        try:
            import gevent
        except ImportError:
            gevent = None
        while _model is None and _model_loading:
            if gevent is not None:
                gevent.sleep(0.3)
            else:
                import time
                time.sleep(0.3)
        return _model if _model else None
    _model_loading = True
    try:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(MODEL_NAME, local_files_only=True)
            print("[semantic] 本地 embedding 模型加载成功")
        except Exception as e:
            print(f"[semantic] 本地模型不可用，降级 API embedding: {e}")
            _model = False
    finally:
        _model_loading = False
    return _model if _model else None


def warmup():
    """启动预热：初始化向量存储 + 加载本地 embedding 模型。
    避免首次发帖/搜索触发 10s+ 的同步模型加载导致服务器请求排队（偶发超时根因）。"""
    try:
        _store_init()
    except Exception as e:
        print(f"[semantic] 预热存储失败：{e}")
    try:
        _get_model()
    except Exception as e:
        print(f"[semantic] 预热模型失败：{e}")
    print("[semantic] 预热完成")


def _embed_api(texts):
    """降级：OpenAI 兼容 embedding API（siliconflow），每次调用后 sleep 控频。"""
    import json
    import urllib.request
    from config import AI_CONFIG
    api_key = (AI_CONFIG.get("api_key") or "").strip()
    base = (AI_CONFIG.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    if not api_key:
        raise RuntimeError("embedding API key 未配置")
    payload = json.dumps({"model": "BAAI/bge-m3", "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        base + "/embeddings", data=payload,
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    emb = [None] * len(texts)
    for item in data.get("data", []):
        emb[item["index"]] = item["embedding"]
    if any(x is None for x in emb):
        raise RuntimeError("embedding API 返回不完整")
    time.sleep(EMBED_API_SLEEP)
    return emb


def _embed(texts):
    """生成 embedding：本地模型优先，API 降级。"""
    m = _get_model()
    if m is not None:
        try:
            return m.encode(texts, normalize_embeddings=True).tolist()
        except Exception as e:
            print(f"[semantic] 本地模型推理失败，降级 API: {e}")
    return _embed_api(texts)


# ========== 统一向量存储层（ChromaDB 优先，numpy fallback） ==========
_store = None       # "chroma" | "numpy" | None
_chroma_col = None
_fb_ids, _fb_embs, _fb_metas = [], None, []


def _store_init():
    """初始化存储层。优先 ChromaDB；失败降级内置 numpy 存储。"""
    global _store, _chroma_col, _fb_ids, _fb_embs, _fb_metas
    if _store is not None:
        return
    # 尝试 ChromaDB
    try:
        import chromadb
        os.makedirs(VECTOR_DIR, exist_ok=True)
        _chroma_col = chromadb.PersistentClient(
            path=VECTOR_DIR).get_or_create_collection(
            name="posts", metadata={"hnsw:space": "cosine"})
        _store = "chroma"
        print("[semantic] 向量存储：ChromaDB")
        return
    except Exception as e:
        print(f"[semantic] ChromaDB 不可用，降级内置 numpy 向量存储：{e}")
    # 降级 numpy
    try:
        import numpy as np
        fb = os.path.join(VECTOR_DIR, "fallback.npz")
        if os.path.exists(fb):
            d = np.load(fb, allow_pickle=True)
            _fb_ids = list(d["ids"])
            _fb_embs = np.asarray(d["embs"], dtype="float32")
            _fb_metas = list(d["metas"])
        _store = "numpy"
        print("[semantic] 向量存储：内置 numpy")
    except Exception as e:
        print(f"[semantic] 向量存储初始化失败：{e}")
        _store = False


def _fb_save():
    import numpy as np
    os.makedirs(VECTOR_DIR, exist_ok=True)
    if _fb_embs is None:
        return
    np.savez(os.path.join(VECTOR_DIR, "fallback.npz"),
             ids=np.array(_fb_ids), embs=_fb_embs, metas=np.array(_fb_metas))


def vector_store_ready():
    """向量库是否可用（未禁用且存储层初始化成功）。"""
    if DISABLED:
        return False
    _store_init()
    return bool(_store)


def count():
    _store_init()
    if _store == "chroma":
        try:
            return _chroma_col.count()
        except Exception:
            return 0
    if _store == "numpy":
        return len(_fb_ids)
    return 0


def _store_clear():
    global _fb_ids, _fb_embs, _fb_metas
    if _store == "chroma":
        try:
            # ChromaDB delete(where={}) 不匹配任何记录，须按 id 全删
            ids = _chroma_col.get()["ids"]
            if ids:
                _chroma_col.delete(ids=ids)
        except Exception:
            pass
    elif _store == "numpy":
        _fb_ids, _fb_embs, _fb_metas = [], None, []
        _fb_save()


def _store_add(ids, embs, metas):
    global _fb_ids, _fb_embs, _fb_metas
    with _write_lock:
        if _store == "chroma":
            _chroma_col.add(ids=ids, embeddings=embs, metadatas=metas)
        else:
            import numpy as np
            embs = np.asarray(embs, dtype="float32")
            if _fb_embs is None:
                _fb_embs = embs
            else:
                _fb_embs = np.vstack([_fb_embs, embs])
            _fb_ids.extend(ids)
            _fb_metas.extend(metas)
            _fb_save()


def _store_update(ids, embs, metas):
    global _fb_ids, _fb_embs, _fb_metas
    with _write_lock:
        if _store == "chroma":
            _chroma_col.update(ids=ids, embeddings=embs, metadatas=metas)
        else:
            for pid, emb, meta in zip(ids, embs, metas):
                if pid in _fb_ids:
                    i = _fb_ids.index(pid)
                    if _fb_embs is not None:
                        _fb_embs[i] = emb
                    _fb_metas[i] = meta
                else:
                    _store_add([pid], [emb], [meta])
            _fb_save()


def _store_delete(ids):
    global _fb_ids, _fb_embs, _fb_metas
    with _write_lock:
        if _store == "chroma":
            try:
                _chroma_col.delete(ids=ids)
            except Exception:
                pass
        else:
            for pid in ids:
                if pid in _fb_ids:
                    i = _fb_ids.index(pid)
                    _fb_ids.pop(i)
                    _fb_metas.pop(i)
                    if _fb_embs is not None:
                        _fb_embs = _fb_embs[[j for j in range(_fb_embs.shape[0]) if j != i]]
            _fb_save()


def _store_query(vec, n):
    """返回 (ids, scores, metas) 按相似度降序。"""
    import numpy as np
    if _store == "chroma":
        r = _chroma_col.query(query_embeddings=[vec], n_results=n)
        ids = r.get("ids", [[]])[0]
        dists = r["distances"][0]
        metas = r["metadatas"][0] if r.get("metadatas") else [{}] * len(ids)
        scores = [1 - d for d in dists]
        return ids, scores, metas
    if _store == "numpy" and _fb_embs is not None and len(_fb_ids):
        v = np.asarray(vec, dtype="float32")
        sims = _fb_embs @ v  # 已归一化 → 余弦相似度
        order = np.argsort(sims)[::-1][:n]
        ids = [_fb_ids[i] for i in order]
        scores = [float(sims[i]) for i in order]
        metas = [_fb_metas[i] for i in order]
        return ids, scores, metas
    return [], [], []


# ========== 索引维护 ==========
def _add_batch(ids, texts, metas):
    _store_add(ids, _embed(texts), metas)


def index_post(post_id, title, content):
    """帖子发布时加入索引（公开帖）。调用方需确保 is_private=0。失败不抛错。"""
    _store_init()
    if not _store:
        return False
    try:
        _add_batch([str(post_id)], [(title + " " + content)[:2000]],
                   [{"title": title[:120], "summary": content[:120]}])
        return True
    except Exception as e:
        print(f"[semantic] index_post 失败: {e}")
        return False


def update_post_index(post_id, title, content, is_private=False):
    """帖子编辑时更新索引；转为私密帖则从索引移除。"""
    _store_init()
    if not _store:
        return False
    try:
        if is_private:
            _store_delete([str(post_id)])
            return True
        _store_update([str(post_id)], _embed([(title + " " + content)[:2000]]),
                      [{"title": title[:120], "summary": content[:120]}])
        return True
    except Exception as e:
        print(f"[semantic] update_post_index 失败: {e}")
        return False


def remove_post(post_id):
    _store_init()
    if not _store:
        return False
    try:
        _store_delete([str(post_id)])
        return True
    except Exception:
        return False


def _rebuild_index_locked():
    """全量重建索引（分批 + sleep）。调用方须已持有进程锁与线程锁。"""
    _store_init()
    if not _store:
        return 0
    _store_clear()
    n = 0
    batch_ids, batch_texts, batch_metas = [], [], []
    with get_cursor() as cur:
        cur.execute("""
            SELECT p.post_id, p.title, p.content, u.status, u.status
            FROM post p JOIN `user` u ON p.author_id = u.user_id
            WHERE p.is_private = 0
        """)
        for row in cur:
            pid, title, content, status, _ = row
            if status != 0:
                continue
            batch_ids.append(str(pid))
            batch_texts.append((title + " " + (content or ""))[:2000])
            batch_metas.append({"title": (title or "")[:120],
                                "summary": (content or "")[:120]})
            if len(batch_ids) >= BATCH_SIZE:
                _add_batch(batch_ids, batch_texts, batch_metas)
                n += len(batch_ids)
                batch_ids, batch_texts, batch_metas = [], [], []
                time.sleep(0.5)
    if batch_ids:
        _add_batch(batch_ids, batch_texts, batch_metas)
        n += len(batch_ids)
    print(f"[semantic] 索引重建完成，共 {n} 帖")
    return n


def rebuild_index():
    """全量重建索引（分批 + sleep）。返回索引帖子数；向量库不可用返回 0。
    进程锁（backend/vector_store/.rebuild.lock）保证跨进程串行：
    应用后台重建与外部脚本/测试并发调用时，后到者拿不到锁直接跳过并记录日志，
    避免并发清空/写入导致 ChromaDB 段损坏；线程锁补充同进程多线程互斥。"""
    os.makedirs(VECTOR_DIR, exist_ok=True)
    try:
        lock = FileLock(REBUILD_LOCK_PATH)
        with lock.acquire(timeout=0):
            with _rebuild_lock:
                return _rebuild_index_locked()
    except Timeout:
        print("[semantic] 重建进行中（另一进程持有锁），本次跳过")
        return 0


# ========== 语义搜索 ==========
def semantic_search(query, top=10, threshold=0.5):
    """向量检索 Top-N，过滤低于相似度阈值的结果。向量库不可用抛 RuntimeError。"""
    if DISABLED:
        raise RuntimeError("向量库不可用")
    _store_init()
    if not _store:
        raise RuntimeError("向量库不可用")
    vec = _embed([query])[0]
    ids, scores, metas = _store_query(vec, min(top * 3, 30))
    out = []
    for pid, sim, meta in zip(ids, scores, metas):
        if sim < threshold:
            continue
        meta = meta or {}
        out.append({"post_id": int(pid), "title": meta.get("title", ""),
                    "summary": meta.get("summary", ""),
                    "similarity": round(sim, 4)})
    return out[:top]


def search_semantic_posts(query, top=10, threshold=0.5):
    """语义搜索 + DB 回源校验（私密帖/已封禁作者/停用用户剔除）。返回列表。
    回源逐条参数化查询（结果 ≤ top=10 条，避免动态 IN 子句触发静态扫描误报）。"""
    hits = semantic_search(query, top, threshold)
    if not hits:
        return []
    valid = {}
    with get_cursor() as cur:
        for h in hits:
            cur.execute("""
                SELECT p.post_id, p.title, u.nickname, p.create_time, p.view_count,
                       (SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS reply_count
                FROM post p JOIN `user` u ON p.author_id = u.user_id
                WHERE p.post_id = ? AND p.is_private = 0 AND u.status = 0
            """, (h["post_id"],))
            row = cur.fetchone()
            if row:
                valid[row[0]] = row
    out = []
    for h in hits:
        row = valid.get(h["post_id"])
        if not row:
            continue
        out.append({"post_id": row[0], "title": row[1], "author": row[2],
                    "create_time": str(row[3])[:16] if row[3] else "",
                    "view_count": row[4], "reply_count": row[5],
                    "similarity": h["similarity"]})
    return out
