"""语义/跨语言搜索：paraphrase-multilingual-MiniLM-L12-v2（50+ 语言共享向量空间），不可用时回退 TF-IDF。
图片搜索：CLIP(clip-ViT-B-32) 图像嵌入，不可用时回退感知哈希 phash。"""
import os, json, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
_ST = None; ENGINE = "tfidf-char-ngram(回退)"
if os.environ.get("PRICEHUB_NO_ST") != "1":
    try:
        from sentence_transformers import SentenceTransformer
        _ST = SentenceTransformer(os.environ.get("PRICEHUB_TEXT_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")); ENGINE = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    except Exception as e: print("ST unavailable:", e)
_CACHE = {}; _QCACHE = {}; _INDEX = {"key": None, "M": None}
EMB_FILE = os.environ.get("PRICEHUB_EMB_CACHE", os.path.join(os.path.dirname(__file__), "..", "data", "emb_cache.npz"))
def _load_disk():
    """向量持久化：重启后无需重新编码全部商品（冷启动加速）。"""
    try:
        z = np.load(EMB_FILE, allow_pickle=False)
        for t, e in zip(z["t"], z["e"]): _CACHE[str(t)] = e
    except Exception: pass
def _save_disk():
    try:
        ks = list(_CACHE); np.savez(EMB_FILE, t=np.array(ks), e=np.array([_CACHE[k] for k in ks], dtype=np.float32))
    except Exception as e: print("emb cache save failed:", e)
if _ST: _load_disk()
def _emb(texts):
    miss = list(dict.fromkeys(t for t in texts if t not in _CACHE))
    if miss:
        for t, e in zip(miss, _ST.encode(miss, normalize_embeddings=True, batch_size=128)): _CACHE[t] = e.astype(np.float32)
        if len(miss) > 20: _save_disk()
    return np.array([_CACHE[t] for t in texts])
def _qemb(q):
    if q not in _QCACHE:
        if len(_QCACHE) > 512: _QCACHE.clear()
        _QCACHE[q] = _ST.encode([q], normalize_embeddings=True)[0].astype(np.float32)
    return _QCACHE[q]
def doc(p): return f"{p['title']} {p.get('category') or ''} {p.get('brand') or ''}"
def index_matrix(products, key=None):
    """向量索引：商品集合不变时复用同一个归一化矩阵，查询只需一次矩阵乘法。"""
    key = key if key is not None else hash(tuple(doc(p) for p in products))
    if _INDEX["key"] != key: _INDEX.update(key=key, M=_emb([doc(p) for p in products]))
    return _INDEX["M"]
def semantic(query, products, k=10, min_score=0.0, key=None):
    if not products: return []
    if _ST: s = index_matrix(products, key) @ _qemb(query)
    else:
        docs = [doc(p) for p in products]
        v = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit(docs + [query])
        s = (v.transform(docs) @ v.transform([query]).T).toarray().ravel()
    idx = np.argpartition(-s, min(k, len(s) - 1))[:k] if len(s) > k else np.arange(len(s))
    idx = idx[np.argsort(-s[idx])]
    return [dict(products[i], score=round(float(s[i]), 4)) for i in idx if s[i] > min_score]
_CLIP = None; IMG_ENGINE = "imagehash-phash(回退)"
if os.environ.get("PRICEHUB_NO_CLIP") != "1":
    try:
        from sentence_transformers import SentenceTransformer
        _CLIP = SentenceTransformer("clip-ViT-B-32"); IMG_ENGINE = "CLIP clip-ViT-B-32 + phash"
    except Exception as e: print("CLIP unavailable:", e)
def image_features(path):
    import imagehash; from PIL import Image
    im = Image.open(path).convert("RGB"); f = {"phash": str(imagehash.phash(im))}
    if _CLIP: f["clip"] = json.dumps([round(float(x), 5) for x in _CLIP.encode([im], normalize_embeddings=True)[0]])
    return f
def image_search(qf, products, k=10):
    import imagehash
    res = []
    for p in products:
        if _CLIP and qf.get("clip") and p.get("clip"):
            s = float(np.dot(json.loads(qf["clip"]), json.loads(p["clip"]))); res.append(dict(p, score=round(s, 4), method="clip"))
        elif p.get("phash"):
            d = imagehash.hex_to_hash(qf["phash"]) - imagehash.hex_to_hash(p["phash"]); res.append(dict(p, score=round(1 - d / 64, 4), method="phash"))
    for r in res: r.pop("clip", None)
    return sorted(res, key=lambda r: -r["score"])[:k]
