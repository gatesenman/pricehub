"""语义/跨语言搜索：paraphrase-multilingual-MiniLM-L12-v2（50+ 语言共享向量空间），不可用时回退 TF-IDF。
图片搜索：CLIP(clip-ViT-B-32) 图像嵌入，不可用时回退感知哈希 phash。

v14：ST / CLIP 改为首次真正用到时再加载，避免冷启动把模型装进内存拖慢 /readyz。
"""
import os, json, threading, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from app.core.vector import (
    VectorIndex, HAVE_FAISS, FallbackIndex,
    content_digest, make_signature, save_vector_index, load_vector_index, remove_index,
)

_ST = None
_CLIP = None
_ST_TRIED = False
_CLIP_TRIED = False
_ST_LOADING = False
_CLIP_LOADING = False
_ST_LOCK = threading.Lock()
_CLIP_LOCK = threading.Lock()
ENGINE = "tfidf-char-ngram(回退)"
IMG_ENGINE = "imagehash-phash(回退)"
VECTOR_ENGINE = "faiss-hnsw(≥5万) / numpy 精确(<5万)" if HAVE_FAISS else "numpy"
_CACHE = {}
_QCACHE = {}
_INDEX = {"key": None, "M": None, "vi": None, "source": None, "sig": None}
SEARCH_INDEX = "search"
EMB_FILE = os.environ.get(
    "PRICEHUB_EMB_CACHE",
    os.path.join(os.path.dirname(__import__("app").__file__), "..", "data", "emb_cache.npz"),
)


def _want_st() -> bool:
    return os.environ.get("PRICEHUB_NO_ST") != "1"


def _want_clip() -> bool:
    return os.environ.get("PRICEHUB_NO_CLIP") != "1"


def _ensure_st():
    """首次语义搜索时再加载多语言模型。"""
    global _ST, _ST_TRIED, _ST_LOADING, ENGINE
    if _ST is not None or _ST_TRIED or not _want_st():
        return _ST
    with _ST_LOCK:
        if _ST is not None or _ST_TRIED:
            return _ST
        _ST_LOADING = True
        try:
            from sentence_transformers import SentenceTransformer
            _ST = SentenceTransformer(os.environ.get("PRICEHUB_TEXT_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"))
            ENGINE = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            _load_disk()
        except Exception as e:
            print("ST unavailable:", e, flush=True)
            _ST = None
        finally:
            _ST_LOADING = False
            _ST_TRIED = True
    return _ST


def _ensure_clip():
    """首次以图搜图时再加载 CLIP。"""
    global _CLIP, _CLIP_TRIED, _CLIP_LOADING, IMG_ENGINE
    if _CLIP is not None or _CLIP_TRIED or not _want_clip():
        return _CLIP
    with _CLIP_LOCK:
        if _CLIP is not None or _CLIP_TRIED:
            return _CLIP
        _CLIP_LOADING = True
        try:
            from sentence_transformers import SentenceTransformer
            _CLIP = SentenceTransformer("clip-ViT-B-32")
            IMG_ENGINE = "CLIP clip-ViT-B-32 + phash"
        except Exception as e:
            print("CLIP unavailable:", e, flush=True)
            _CLIP = None
        finally:
            _CLIP_LOADING = False
            _CLIP_TRIED = True
    return _CLIP


def vector_engine_now():
    vi = _INDEX.get("vi")
    return getattr(vi, "engine", None) or VECTOR_ENGINE


def _load_disk():
    """向量持久化：重启后无需重新编码全部商品（冷启动加速）。"""
    try:
        z = np.load(EMB_FILE, allow_pickle=False)
        for t, e in zip(z["t"], z["e"]):
            _CACHE[str(t)] = e
    except Exception:
        pass


def _save_disk():
    try:
        ks = list(_CACHE)
        np.savez(EMB_FILE, t=np.array(ks), e=np.array([_CACHE[k] for k in ks], dtype=np.float32))
    except Exception as e:
        print("emb cache save failed:", e)


def _emb(texts):
    st = _ensure_st()
    miss = list(dict.fromkeys(t for t in texts if t not in _CACHE))
    if miss:
        for t, e in zip(miss, st.encode(miss, normalize_embeddings=True, batch_size=128)):
            _CACHE[t] = e.astype(np.float32)
        if len(miss) > 20:
            _save_disk()
    return np.array([_CACHE[t] for t in texts])


def _qemb(q):
    st = _ensure_st()
    if q not in _QCACHE:
        if len(_QCACHE) > 512:
            _QCACHE.clear()
        _QCACHE[q] = st.encode([q], normalize_embeddings=True)[0].astype(np.float32)
    return _QCACHE[q]


def doc(p):
    return f"{p['title']} {p.get('category') or ''} {p.get('brand') or ''}"


def _docs_ids(products):
    ids = [int(p["id"]) if p.get("id") is not None else i for i, p in enumerate(products)]
    docs = [doc(p) for p in products]
    return ids, docs


def search_signature(products, dim=None, model=None):
    ids, docs = _docs_ids(products)
    return make_signature(
        name=SEARCH_INDEX, kind="auto", dim=dim, n=len(products),
        digest=content_digest(ids, texts=docs),
        model=model if model is not None else (ENGINE if _ST is not None else ""),
    )


def _active_index():
    vi = _INDEX.get("vi")
    if vi is None:
        return None
    if isinstance(vi, FallbackIndex):
        return vi.fallback or vi.primary
    return vi


def index_matrix(products, key=None):
    """向量索引：商品集合（id+标题等）不变时复用；优先加载磁盘 FAISS，否则重建并落盘。
    缓存键用内容签名，不再跟 db.VER 走，避免每次记价格都重建索引。"""
    if not products:
        return None
    sig = search_signature(products)
    digest = sig["digest"]
    if _INDEX.get("vi") is not None and _INDEX.get("sig") and _INDEX["sig"].get("digest") == digest:
        return _INDEX["M"]
    loaded, reason = load_vector_index(SEARCH_INDEX, expected=sig)
    if loaded is not None:
        _INDEX.update(key=key if key is not None else digest, M=loaded.M, vi=loaded, source="disk", sig=sig)
        return _INDEX["M"]
    M = _emb([doc(p) for p in products])
    vi = FallbackIndex(len(M)).build(M)
    inner = vi.fallback or vi.primary
    if isinstance(inner, VectorIndex):
        inner.ids = np.asarray(_docs_ids(products)[0], dtype=np.int64)
        try:
            save_vector_index(inner, SEARCH_INDEX, {**sig, "dim": int(M.shape[1]), "model": ENGINE})
        except Exception as e:
            print("faiss save failed:", e)
    _INDEX.update(key=key if key is not None else digest, M=M, vi=vi, source="build:" + reason, sig=sig)
    return _INDEX["M"]


def vector_index(products, key=None):
    index_matrix(products, key)
    return _INDEX["vi"]


def invalidate_index(remove_disk=True):
    """商品删除或标题变更后失效内存/磁盘索引，下次搜索或预热再重建。"""
    _INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
    if remove_disk:
        try: remove_index(SEARCH_INDEX)
        except Exception: pass


def on_products_changed(added=None, removed=None, updated=None, products=None):
    """写入/删除商品时更新索引。删除或改标题：失效（HNSW 不便按 id 删除）。纯新增：能增量则 add 并落盘。"""
    added = list(added or [])
    removed = list(removed or [])
    updated = list(updated or [])
    if removed or updated:
        invalidate_index(remove_disk=True)
        return {"action": "invalidate", "reason": "removed" if removed else "updated"}
    if not added:
        return {"action": "noop"}
    vi = _active_index()
    if vi is None or not isinstance(vi, VectorIndex) or _ST is None:
        invalidate_index(remove_disk=True)
        return {"action": "invalidate", "reason": "no-live-index"}
    try:
        docs = [doc(p) if isinstance(p, dict) else str(p) for p in added]
        ids = [int(p["id"]) if isinstance(p, dict) and p.get("id") is not None else i for i, p in enumerate(added)]
        E = _emb(docs)
        vi.add(E, ids)
        if _INDEX.get("M") is not None:
            _INDEX["M"] = np.vstack([_INDEX["M"], E])
        if products is not None:
            sig = search_signature(products, dim=E.shape[1], model=ENGINE)
        else:
            # 尚无完整列表时，用旧签名加新文本，保证磁盘与内存一致；下次完整加载会再校验
            old = _INDEX.get("sig") or {}
            sig = dict(old)
            sig["n"] = int(sig.get("n") or 0) + len(added)
            sig["digest"] = content_digest(list(vi.ids), texts=None, vectors=vi.M if vi.M is not None else _INDEX.get("M"))
        _INDEX["sig"] = sig
        _INDEX["key"] = sig.get("digest")
        _INDEX["source"] = "add"
        save_vector_index(vi, SEARCH_INDEX, {**sig, "dim": int(E.shape[1]), "model": ENGINE})
        return {"action": "add", "n": vi.size()}
    except Exception as e:
        invalidate_index(remove_disk=True)
        return {"action": "invalidate", "reason": str(e)}


def warm_persisted_index(products=None):
    """启动时加载已有索引。签名与当前商品一致才用，否则留给首次搜索再重建。"""
    if not products:
        return {"action": "skip", "reason": "no-products"}
    if _ST is None and not _want_st():
        return {"action": "skip", "reason": "no-st"}
    # 若尚未加载模型但磁盘索引只依赖已缓存向量，仍可先试加载（不触发编码）
    sig = search_signature(products)
    loaded, reason = load_vector_index(SEARCH_INDEX, expected={k: sig[k] for k in ("format", "name", "n", "digest") if k in sig} | {"kind": "auto"})
    if loaded is not None:
        _INDEX.update(key=sig["digest"], M=loaded.M, vi=loaded, source="disk", sig=sig)
        return {"action": "load", "n": loaded.size(), "engine": loaded.engine, "reason": reason}
    return {"action": "miss", "reason": reason, "n": len(products)}


def semantic(query, products, k=10, min_score=0.0, key=None):
    if not products:
        return []
    if _ensure_st():
        vi = vector_index(products, key)
        I, D = vi.search(_qemb(query), k)
        return [dict(products[int(i)], score=round(float(d), 4)) for i, d in zip(I, D) if d > min_score]
    docs = [doc(p) for p in products]
    v = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit(docs + [query])
    s = (v.transform(docs) @ v.transform([query]).T).toarray().ravel()
    idx = np.argpartition(-s, min(k, len(s) - 1))[:k] if len(s) > k else np.arange(len(s))
    idx = idx[np.argsort(-s[idx])]
    return [dict(products[i], score=round(float(s[i]), 4)) for i in idx if s[i] > min_score]


def image_features(path):
    import imagehash
    from PIL import Image
    im = Image.open(path).convert("RGB")
    f = {"phash": str(imagehash.phash(im))}
    clip = _ensure_clip()
    if clip:
        f["clip"] = json.dumps([round(float(x), 5) for x in clip.encode([im], normalize_embeddings=True)[0]])
    return f


def image_search(qf, products, k=10):
    import imagehash
    res = []
    clip = _CLIP  # 已在 image_features 里 ensure；查询侧若无 clip 向量则用 phash
    for p in products:
        if clip and qf.get("clip") and p.get("clip"):
            s = float(np.dot(json.loads(qf["clip"]), json.loads(p["clip"])))
            res.append(dict(p, score=round(s, 4), method="clip"))
        elif p.get("phash"):
            d = imagehash.hex_to_hash(qf["phash"]) - imagehash.hex_to_hash(p["phash"])
            res.append(dict(p, score=round(1 - d / 64, 4), method="phash"))
    for r in res:
        r.pop("clip", None)
    return sorted(res, key=lambda r: -r["score"])[:k]


# 健康检查用：不触发加载，只报告当前/预期引擎名
def engine_status():
    if _ST is not None:
        text = ENGINE
    elif _ST_LOADING:
        text = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2（加载中）"
    elif not _want_st() or _ST_TRIED:
        text = "tfidf-char-ngram(回退)"
    else:
        text = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2（按需加载）"
    if _CLIP is not None:
        img = IMG_ENGINE
    elif _CLIP_LOADING:
        img = "CLIP clip-ViT-B-32 + phash（加载中）"
    elif not _want_clip() or _CLIP_TRIED:
        img = "imagehash-phash(回退)"
    else:
        img = "CLIP clip-ViT-B-32 + phash（按需加载）"
    return text, img
