"""向量索引抽象：默认 FAISS（内积 = 归一化后的余弦相似度），不可用时回退 numpy 矩阵乘法。
小规模（< HNSW_MIN）用精确 IndexFlatIP；大规模自动换 HNSW 近似索引，查询从 O(N) 降到约 O(log N)。
接口：build(vectors, ids) / search(q, k) -> [(id, score)] / add / size。将来可换 pgvector/Milvus 而不改调用方。

v15：FAISS / numpy 索引可按签名落盘。签名含商品 id + 文本（或向量）摘要、维度、引擎与参数；
重启后签名一致则直接加载，不必重建；商品写入可增量 add，删除或改标题则失效并重建。
"""
import os, json, hashlib, tempfile, time, numpy as np
try:
    import faiss; faiss.omp_set_num_threads(int(os.environ.get("PRICEHUB_FAISS_THREADS", "2"))); HAVE_FAISS = True
except Exception: faiss = None; HAVE_FAISS = False
HNSW_MIN = int(os.environ.get("PRICEHUB_HNSW_MIN", "50000"))
FORMAT = "pricehub-faiss-v1"


def default_dir():
    """默认落盘目录：PRICEHUB_FAISS_DIR 优先；否则跟 SQLite 库放在一起（测试库互不污染）。"""
    env = os.environ.get("PRICEHUB_FAISS_DIR")
    if env:
        return env
    db = os.environ.get("PRICEHUB_DB")
    if db:
        return db + ".faiss"
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "faiss"
    )


def content_digest(ids, texts=None, vectors=None):
    """稳定摘要：id 序列 + 文本（优先）或向量字节。用于发现索引失效。"""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(np.asarray(list(ids), dtype=np.int64)).tobytes())
    if texts is not None:
        for t in texts:
            h.update(b"\n")
            h.update((t or "").encode("utf-8", "surrogatepass"))
    elif vectors is not None:
        h.update(np.ascontiguousarray(np.asarray(vectors, dtype=np.float32)).tobytes())
    return h.hexdigest()


def make_signature(*, name, kind, dim, n, digest, params=None, model=""):
    return {
        "format": FORMAT, "name": name, "kind": kind, "dim": int(dim or 0),
        "n": int(n or 0), "digest": digest, "params": params or {}, "model": model or "",
    }


def signatures_match(stored, expected):
    """expected 里 kind=auto / 缺省字段不参与比较，避免尚未建索引时无法预知精确/HNSW。"""
    if not stored or not expected:
        return False
    if stored.get("format") != FORMAT or stored.get("name") != expected.get("name"):
        return False
    if int(stored.get("n") or 0) != int(expected.get("n") or 0):
        return False
    if stored.get("digest") != expected.get("digest"):
        return False
    if expected.get("dim") and int(stored.get("dim") or 0) != int(expected["dim"]):
        return False
    ek = expected.get("kind")
    if ek and ek not in ("auto", stored.get("kind")):
        return False
    if expected.get("params") and stored.get("params") != expected.get("params"):
        return False
    if expected.get("model") and stored.get("model") != expected.get("model"):
        return False
    return True


def index_paths(name, root=None):
    root = root or default_dir()
    base = os.path.join(root, name)
    return {"root": root, "base": base, "meta": base + ".meta.json", "index": base + ".index",
            "vecs": base + ".vecs.npy", "ids": base + ".ids.npy"}


def _atomic_write_bytes(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


def _atomic_npy(path, arr):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".npy", dir=d)
    os.close(fd)
    try:
        np.save(tmp, arr); os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _hnsw_m(index):
    h = index.hnsw
    if hasattr(h, "M"):
        return int(h.M)
    try:
        # faiss 1.15+ 不再暴露 M；0 层邻接为 2M，更高层为 M
        return int(h.nb_neighbors(1))
    except Exception:
        return 32


def _index_params(vi):
    p = {}
    if HAVE_FAISS and vi.index is not None and isinstance(vi.index, faiss.IndexHNSWFlat):
        p = {"hnsw_m": _hnsw_m(vi.index), "efConstruction": int(vi.index.hnsw.efConstruction),
             "efSearch": int(vi.index.hnsw.efSearch)}
    return p


class VectorIndex:
    def __init__(self, dim=None, kind="auto"):
        self.dim, self.kind, self.index, self.M, self.ids = dim, kind, None, None, np.zeros(0, dtype=np.int64)
    @property
    def engine(self):
        if self.index is None: return "numpy-exact"
        return "faiss-hnsw" if isinstance(self.index, faiss.IndexHNSWFlat) else "faiss-flat"
    def build(self, vectors, ids=None, keep_vectors=False):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32)); n = len(X)
        self.dim = X.shape[1] if n else self.dim; self.ids = np.arange(n, dtype=np.int64) if ids is None else np.asarray(ids, dtype=np.int64)
        # 压测结论：单条查询时 numpy 精确矩阵乘（多线程 BLAS）比 FAISS Flat 更快，所以小规模走 numpy 精确检索，大规模走 FAISS HNSW。
        if not HAVE_FAISS or n == 0 or (self.kind == "auto" and n < HNSW_MIN) or self.kind == "numpy":
            self.M = X; self.index = None; return self
        use_hnsw = self.kind == "hnsw" or (self.kind == "auto" and n >= HNSW_MIN)
        if use_hnsw:
            self.index = faiss.IndexHNSWFlat(self.dim, 32, faiss.METRIC_INNER_PRODUCT); self.index.hnsw.efConstruction = 80; self.index.hnsw.efSearch = 64
        else: self.index = faiss.IndexFlatIP(self.dim)
        self.index.add(X)
        if keep_vectors: self.M = X
        return self
    def add(self, vectors, ids):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
        if self.index is None and self.M is not None: self.M = np.vstack([self.M, X])
        elif self.index is None: return self.build(X, ids)
        else: self.index.add(X)
        if self.M is not None and self.index is not None:
            self.M = np.vstack([self.M, X])
        self.ids = np.concatenate([self.ids, np.asarray(ids, dtype=np.int64)]); return self
    def size(self): return int(len(self.ids))
    def search(self, q, k=10):
        """返回 (位置下标数组, 分数数组)，按分数降序。"""
        n = self.size()
        if n == 0: return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        k = min(k, n); q = np.ascontiguousarray(np.asarray(q, dtype=np.float32).reshape(1, -1))
        if self.index is not None:
            D, I = self.index.search(q, k); m = I[0] >= 0; return I[0][m], D[0][m]
        s = self.M @ q[0]; idx = np.argpartition(-s, k - 1)[:k] if n > k else np.arange(n); idx = idx[np.argsort(-s[idx])]; return idx, s[idx]


def save_vector_index(vi, name, signature=None, root=None, store_vectors=False):
    """原子落盘：meta.json + ids.npy +（FAISS .index 和/或 vecs.npy）。"""
    p = index_paths(name, root); os.makedirs(p["root"], exist_ok=True)
    meta = dict(signature or {})
    meta.update({
        "format": FORMAT, "name": name, "kind": vi.engine, "dim": int(vi.dim or 0), "n": vi.size(),
        "params": _index_params(vi), "saved_at": time.time(),
        "has_index": vi.index is not None, "has_vecs": bool(store_vectors or (vi.index is None and vi.M is not None)),
    })
    if "digest" not in meta:
        meta["digest"] = content_digest(vi.ids, vectors=vi.M if vi.M is not None else None)
    if vi.index is not None:
        if not HAVE_FAISS: raise RuntimeError("FAISS unavailable")
        fd, tmp = tempfile.mkstemp(prefix=".index.", dir=p["root"]); os.close(fd)
        try:
            faiss.write_index(vi.index, tmp); os.replace(tmp, p["index"])
        except Exception:
            try: os.unlink(tmp)
            except OSError: pass
            raise
    elif os.path.exists(p["index"]):
        try: os.unlink(p["index"])
        except OSError: pass
    X = vi.M if (meta["has_vecs"] and vi.M is not None) else None
    if X is not None:
        _atomic_npy(p["vecs"], np.ascontiguousarray(X, dtype=np.float32))
    elif os.path.exists(p["vecs"]):
        try: os.unlink(p["vecs"])
        except OSError: pass
    _atomic_npy(p["ids"], np.ascontiguousarray(vi.ids, dtype=np.int64))
    _atomic_write_json(p["meta"], meta)
    return meta


def load_vector_index(name, expected=None, root=None):
    """加载已有索引。返回 (VectorIndex 或 None, 原因：ok/missing/mismatch/corrupt/no-faiss)。"""
    p = index_paths(name, root)
    if not os.path.exists(p["meta"]):
        return None, "missing"
    try:
        with open(p["meta"], encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None, "corrupt"
    if expected is not None and not signatures_match(meta, expected):
        return None, "mismatch"
    try:
        kind = "hnsw" if "hnsw" in (meta.get("kind") or "") else ("numpy" if (meta.get("kind") or "").startswith("numpy") else "flat")
        vi = VectorIndex(dim=meta.get("dim"), kind=kind)
        if os.path.exists(p["ids"]):
            vi.ids = np.load(p["ids"])
        else:
            vi.ids = np.arange(int(meta.get("n") or 0), dtype=np.int64)
        vi.dim = int(meta.get("dim") or 0) or vi.dim
        if meta.get("has_index"):
            if not HAVE_FAISS:
                return None, "no-faiss"
            if not os.path.exists(p["index"]):
                return None, "corrupt"
            vi.index = faiss.read_index(p["index"])
            ef = (meta.get("params") or {}).get("efSearch")
            if ef and hasattr(vi.index, "hnsw"):
                vi.index.hnsw.efSearch = int(ef)
        if meta.get("has_vecs") or vi.index is None:
            if not os.path.exists(p["vecs"]):
                if vi.index is None:
                    return None, "corrupt"
            else:
                vi.M = np.ascontiguousarray(np.load(p["vecs"]), dtype=np.float32)
                if vi.index is None:
                    vi.kind = "numpy"
        if vi.size() != int(meta.get("n") or 0):
            return None, "corrupt"
        return vi, "ok"
    except Exception:
        return None, "corrupt"


def remove_index(name, root=None):
    p = index_paths(name, root)
    for k in ("meta", "index", "vecs", "ids"):
        try: os.unlink(p[k])
        except OSError: pass


def make_index(n):
    """v11：按规模与配置选择向量引擎。PRICEHUB_VECTOR_ENGINE=auto|pgvector|faiss|numpy。
    auto：PostgreSQL + pgvector 可用且 n ≥ HNSW_MIN 时用 pgvector，否则 FAISS HNSW / numpy 精确。"""
    eng = os.environ.get("PRICEHUB_VECTOR_ENGINE", "auto")
    if eng == "pgvector" or (eng == "auto" and n >= HNSW_MIN):
        try:
            from app.core import pgvector_store as pv
            if pv.available().get("ok"): return pv.PgVectorIndex()
        except Exception: pass
    return VectorIndex(kind="numpy" if eng == "numpy" else "auto")
class FallbackIndex:
    """包一层：主引擎（pgvector）出错时自动换成 FAISS/numpy 并记录原因。"""
    def __init__(self, n): self.primary = make_index(n); self.fallback = None; self.error = None
    @property
    def engine(self): return (self.fallback or self.primary).engine
    def build(self, M):
        self.M = M
        try: self.primary.build(M)
        except Exception as e: self._fall(e)
        return self
    def _fall(self, e):
        self.error = str(e); self.fallback = VectorIndex().build(self.M)
        import logging; logging.getLogger("pricehub.vector").warning("vector engine fallback", extra={"error": self.error})
    def size(self): return (self.fallback or self.primary).size()
    def search(self, q, k=10):
        if self.fallback: return self.fallback.search(q, k)
        try: return self.primary.search(q, k)
        except Exception as e: self._fall(e); return self.fallback.search(q, k)
    def add(self, vectors, ids):
        inner = self.fallback or self.primary
        if hasattr(inner, "add"):
            inner.add(vectors, ids)
            if getattr(self, "M", None) is not None:
                self.M = np.vstack([self.M, np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))])
        return self
