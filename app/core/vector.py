"""向量索引抽象：默认 FAISS（内积 = 归一化后的余弦相似度），不可用时回退 numpy 矩阵乘法。
小规模（< HNSW_MIN）用精确 IndexFlatIP；大规模自动换 HNSW 近似索引，查询从 O(N) 降到约 O(log N)。
接口：build(vectors, ids) / search(q, k) -> [(id, score)] / add / size。将来可换 pgvector/Milvus 而不改调用方。"""
import os, numpy as np
try:
    import faiss; faiss.omp_set_num_threads(int(os.environ.get("PRICEHUB_FAISS_THREADS", "2"))); HAVE_FAISS = True
except Exception: faiss = None; HAVE_FAISS = False
HNSW_MIN = int(os.environ.get("PRICEHUB_HNSW_MIN", "50000"))
class VectorIndex:
    def __init__(self, dim=None, kind="auto"):
        self.dim, self.kind, self.index, self.M, self.ids = dim, kind, None, None, np.zeros(0, dtype=np.int64)
    @property
    def engine(self):
        if self.index is None: return "numpy-exact"
        return "faiss-hnsw" if isinstance(self.index, faiss.IndexHNSWFlat) else "faiss-flat"
    def build(self, vectors, ids=None):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32)); n = len(X)
        self.dim = X.shape[1] if n else self.dim; self.ids = np.arange(n, dtype=np.int64) if ids is None else np.asarray(ids, dtype=np.int64)
        # 压测结论：单条查询时 numpy 精确矩阵乘（多线程 BLAS）比 FAISS Flat 更快，所以小规模走 numpy 精确检索，大规模走 FAISS HNSW。
        if not HAVE_FAISS or n == 0 or (self.kind == "auto" and n < HNSW_MIN) or self.kind == "numpy": self.M = X; self.index = None; return self
        use_hnsw = self.kind == "hnsw" or (self.kind == "auto" and n >= HNSW_MIN)
        if use_hnsw:
            self.index = faiss.IndexHNSWFlat(self.dim, 32, faiss.METRIC_INNER_PRODUCT); self.index.hnsw.efConstruction = 80; self.index.hnsw.efSearch = 64
        else: self.index = faiss.IndexFlatIP(self.dim)
        self.index.add(X); return self
    def add(self, vectors, ids):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
        if self.index is None and self.M is not None: self.M = np.vstack([self.M, X])
        elif self.index is None: return self.build(X, ids)
        else: self.index.add(X)
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
