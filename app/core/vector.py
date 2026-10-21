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
        if not HAVE_FAISS: return "numpy"
        return "faiss-hnsw" if isinstance(self.index, faiss.IndexHNSWFlat) else "faiss-flat"
    def build(self, vectors, ids=None):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32)); n = len(X)
        self.dim = X.shape[1] if n else self.dim; self.ids = np.arange(n, dtype=np.int64) if ids is None else np.asarray(ids, dtype=np.int64)
        if not HAVE_FAISS or n == 0: self.M = X; self.index = None; return self
        use_hnsw = self.kind == "hnsw" or (self.kind == "auto" and n >= HNSW_MIN)
        if use_hnsw:
            self.index = faiss.IndexHNSWFlat(self.dim, 32, faiss.METRIC_INNER_PRODUCT); self.index.hnsw.efConstruction = 80; self.index.hnsw.efSearch = 64
        else: self.index = faiss.IndexFlatIP(self.dim)
        self.index.add(X); return self
    def add(self, vectors, ids):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
        if self.index is None and not HAVE_FAISS: self.M = X if self.M is None else np.vstack([self.M, X])
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
