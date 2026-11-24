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
FORMAT = "pricehub-faiss-v2"


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
    from app.core import tenancy
    root = os.path.join(root, "ws-" + str(tenancy.current()))
    base = os.path.join(root, name)
    return {"root": root, "base": base, "meta": base + ".meta.json", "index": base + ".index",
            "vecs": base + ".vecs.npy", "ids": base + ".ids.npy", "snapshot": base + ".snapshot.npz"}


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


def _array_hash(arr):
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


def save_vector_index(vi, name, signature=None, root=None, store_vectors=False):
    """Publish ONE immutable generation by atomic replace; readers cannot mix old/new payloads.
    The snapshot contains JSON, ids, vectors and serialized FAISS, never pickle.
    A failed write leaves the previous complete generation untouched.
    """
    p = index_paths(name, root); os.makedirs(p["root"], exist_ok=True)
    ids = np.array(vi.ids, dtype=np.int64, copy=True)
    vectors = np.array(vi.M, dtype=np.float32, copy=True) if vi.M is not None and (store_vectors or vi.index is None) else np.empty((0, int(vi.dim or 0)), np.float32)
    raw = faiss.serialize_index(vi.index) if vi.index is not None else np.empty(0, np.uint8)
    meta = dict(signature or {})
    meta.update(format=FORMAT, name=name, kind=vi.engine, dim=int(vi.dim or 0), n=len(ids),
                index_params=_index_params(vi), saved_at=time.time(), has_index=bool(len(raw)),
                has_vecs=bool(len(vectors)))
    meta.setdefault("digest", content_digest(ids, vectors=vectors))
    meta["checksums"] = {k: _array_hash(v) for k,v in {"ids":ids,"vectors":vectors,"index":raw}.items()}
    fd, tmp = tempfile.mkstemp(prefix=".snapshot-", suffix=".npz", dir=p["root"])
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez(f, meta=np.array(json.dumps(meta)), ids=ids, vectors=vectors, index=raw)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p["snapshot"])
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return meta


def load_vector_index(name, expected=None, root=None):
    p = index_paths(name, root)
    if not os.path.exists(p["snapshot"]): return None, "missing"
    try:
        with np.load(p["snapshot"], allow_pickle=False) as z:
            meta=json.loads(str(z["meta"])); ids=z["ids"]; vectors=z["vectors"]; raw=z["index"]
        if meta.get("format") != FORMAT: return None, "mismatch"
        if expected is not None and not signatures_match(meta, expected): return None, "mismatch"
        if any(_array_hash(v) != meta.get("checksums",{}).get(k) for k,v in {"ids":ids,"vectors":vectors,"index":raw}.items()):
            return None, "corrupt"
        n, dim = int(meta["n"]), int(meta["dim"])
        if ids.dtype != np.int64 or ids.shape != (n,) or len(np.unique(ids)) != n: return None, "corrupt"
        if meta.get("has_vecs"):
            if vectors.shape != (n,dim) or vectors.dtype != np.float32 or not np.isfinite(vectors).all(): return None, "corrupt"
        vi=VectorIndex(dim=dim, kind="numpy")
        vi.ids=ids
        if meta.get("has_index"):
            if not HAVE_FAISS: return None, "no-faiss"
            vi.index=faiss.deserialize_index(np.ascontiguousarray(raw, dtype=np.uint8))
            if vi.index.ntotal != n or vi.index.d != dim: return None, "corrupt"
            if vi.engine != meta.get("kind"): return None, "corrupt"
        else:
            if not meta.get("has_vecs"): return None, "corrupt"
        vi.M=vectors if meta.get("has_vecs") else None
        return vi, "ok"
    except Exception:
        return None, "corrupt"


def remove_index(name, root=None):
    p=index_paths(name,root)
    # Legacy v1 components are disposable caches too.
    for k in ("snapshot","meta","index","vecs","ids"):
        try: os.unlink(p[k])
        except FileNotFoundError: pass


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
