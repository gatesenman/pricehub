"""v13 同款去重的分块（blocking）候选生成：替代 v12 的两两全比较（O(n²)）。

候选对 = 以下几路的并集（每路都只产生"可能同款"的对，再交给 service.score_pair 精确打分）：
1. 条码键：GTIN/EAN/UPC/JAN、ASIN 相同；
2. 品牌+型号键（brand|model）、品牌+系列键（brand|series）；
3. 标题字符 3-gram 的 MinHash/LSH（默认 32 个哈希 = 8 band × 4 row，Jaccard≈0.6 起高概率碰撞）；
4. 向量近邻（ANN）：每个商品取语义 top-k（默认 30，且相似度 ≥0.45）。n ≤ exact_max 时 FAISS IndexFlatIP 精确 top-k，
   更大时 FAISS HNSW 近似 top-k；无多语言模型时为稀疏 TF-IDF 分块乘法（精确）。
   注：不用"≥0.45 范围检索"，因为多语言模型下同领域标题相似度普遍 ≥0.45，范围检索在 10k 合成数据上退化为 4440 万对（≈全比较）。
超大块（例如一个品牌+型号下上千个商品）按 max_block 截断为"排序后滑动窗口"，避免块内 m² 爆炸。
增量：BlockIndex 支持 add() 新商品，并只查询新商品的候选（新采集检测从 O(n²) 降到 O(新×块大小 + 新×k)）。
"""
import zlib, numpy as np
from collections import defaultdict
P = (1 << 61) - 1
def shingles(s, k=3):
    s = (s or "").replace(" ", "")
    if len(s) <= k: return {s} if s else set()
    return {s[i:i + k] for i in range(len(s) - k + 1)}
class MinHasher:
    def __init__(self, num=32, bands=8, seed=7):
        r = np.random.RandomState(seed); self.num, self.bands, self.rows = num, bands, num // bands
        self.a = r.randint(1, 2**31 - 1, size=num, dtype=np.int64); self.b = r.randint(0, 2**31 - 1, size=num, dtype=np.int64)
    def sig(self, text):
        sh = shingles(text)
        if not sh: return None
        h = np.fromiter((zlib.crc32(x.encode()) for x in sh), dtype=np.int64, count=len(sh))
        return ((np.outer(h, self.a) + self.b) % 2147483647).min(axis=0)
    def band_keys(self, sig):
        return [(bi, sig[bi * self.rows:(bi + 1) * self.rows].tobytes()) for bi in range(self.bands)]
def keys_of(f):
    ks = []
    for k, v in (f.get("ids") or {}).items(): ks.append(f"id:{k}:{v}")
    b = f.get("brand") or ""
    if f.get("model"): ks.append(f"bm:{b}|{f['model']}")
    if f.get("series"): ks.append(f"bs:{b}|{f['series']}")
    return ks
def _bucket_codes(members, max_block, n, stats):
    """块内两两对，编码为 i*n+j（i<j）的 int64 数组；超大块改为排序后滑动窗口。"""
    m = np.unique(np.asarray(members, dtype=np.int64))
    if len(m) < 2: return None
    if len(m) <= max_block:
        x, y = np.triu_indices(len(m), 1)
    else:
        stats["oversize_blocks"] = stats.get("oversize_blocks", 0) + 1; w = max(2, max_block // 4)
        x = np.repeat(np.arange(len(m)), w - 1); y = x + np.tile(np.arange(1, w), len(m)); keep = y < len(m); x, y = x[keep], y[keep]
    return m[x] * n + m[y]
def _uniq(chunks):
    """排序去重（原地排序，比 np.unique 的哈希路径省内存）。"""
    if not chunks: return np.zeros(0, np.int64)
    a = np.concatenate(chunks); chunks.clear(); a.sort()
    if len(a) < 2: return a
    keep = np.empty(len(a), bool); keep[0] = True; np.not_equal(a[1:], a[:-1], out=keep[1:]); return a[keep]

class BlockIndex:
    """按位置（0..n-1）建索引。vec 为 L2 归一化的 dense 矩阵（float32），或 scipy 稀疏矩阵（TF-IDF 行归一化）。"""
    def __init__(self, exact_max=20000, k=30, sim_floor=0.45, max_block=200, use_lsh=True, use_ann=True, use_keys=True, hnsw_m=16):
        self.exact_max, self.k, self.sim_floor, self.max_block = exact_max, k, sim_floor, max_block
        self.use_lsh, self.use_ann, self.use_keys, self.hnsw_m = use_lsh, use_ann, use_keys, hnsw_m
        self.mh = MinHasher(); self.key_b = defaultdict(list); self.lsh_b = defaultdict(list); self.n = 0; self.vec = None; self.ann = None
    # ---- 构建 / 增量添加 ----
    def add(self, feats, vec=None):
        base = self.n
        for off, f in enumerate(feats):
            i = base + off
            if self.use_keys:
                for kk in keys_of(f): self.key_b[kk].append(i)
            if self.use_lsh:
                s = self.mh.sig(f.get("norm"))
                if s is not None:
                    for bk in self.mh.band_keys(s): self.lsh_b[bk].append(i)
        self.n += len(feats)
        if vec is not None and self.use_ann: self._add_vec(vec)
        return list(range(base, self.n))
    def _add_vec(self, vec):
        import scipy.sparse as sp
        if sp.issparse(vec):
            self.vec = vec if self.vec is None else sp.vstack([self.vec, vec]).tocsr(); return
        vec = np.ascontiguousarray(vec, dtype=np.float32)
        self.vec = vec if self.vec is None else np.vstack([self.vec, vec])
        if self.n > self.exact_max:
            import faiss
            if self.ann is None or self.ann[0] != "hnsw":
                ix = faiss.IndexHNSWFlat(vec.shape[1], self.hnsw_m, faiss.METRIC_INNER_PRODUCT); ix.hnsw.efConstruction = 40; ix.hnsw.efSearch = max(64, 2 * self.k)
                ix.add(self.vec); self.ann = ("hnsw", ix)
            else: self.ann[1].add(vec)
    # ---- 查询 ----
    def candidate_codes(self, focus=None):
        """返回 (codes:int64 数组，每个元素 i*n+j 且 i<j, stats)。focus=None 全量；否则只要至少一端在 focus 的对。
        用紧凑整数编码而不是 Python 元组集合：1M 商品、数千万候选对时内存从十几 GB 降到几百 MB。"""
        n = self.n; stats = {}; fmask = None
        if focus is not None: fmask = np.zeros(n, bool); fmask[np.asarray(list(focus), dtype=np.int64)] = True
        def run(buckets):
            ch = []
            for mem in buckets:
                if len(mem) < 2 or (fmask is not None and not fmask[mem].any()): continue
                c = _bucket_codes(mem, self.max_block, n, stats)
                if c is None: continue
                if fmask is not None: c = c[fmask[c // n] | fmask[c % n]]
                ch.append(c)
            return _uniq(ch)
        parts = []
        if self.use_keys: k = run(self.key_b.values()); stats["keys"] = int(len(k)); parts.append(k)
        if self.use_lsh: l = run(self.lsh_b.values()); stats["lsh"] = int(len(l)); parts.append(l)
        if self.use_ann and self.vec is not None:
            a = self._ann_codes(None if focus is None else np.flatnonzero(fmask)); stats["ann"] = int(len(a)); parts.append(a)
        allc = _uniq(parts); stats["total"] = int(len(allc)); return allc, stats
    def candidate_pairs(self, focus=None):
        """返回 (pairs:set[(i,j)] i<j, stats)。"""
        c, stats = self.candidate_codes(focus); n = max(1, self.n)
        return set(zip((c // n).tolist(), (c % n).tolist())), stats
    def _ann_codes(self, q):
        import scipy.sparse as sp
        n = self.n; q = np.arange(n) if q is None else np.asarray(q, dtype=np.int64); ch = []
        if len(q) == 0: return np.zeros(0, np.int64)
        def emit(qq, I, D):
            src = np.repeat(qq, I.shape[1]); dst = I.reshape(-1).astype(np.int64); d = D.reshape(-1)
            ok = (dst >= 0) & (dst != src) & (d >= self.sim_floor); a, b = np.minimum(src[ok], dst[ok]), np.maximum(src[ok], dst[ok]); ch.append(a * n + b)
        k = min(self.k + 1, n)
        if sp.issparse(self.vec):      # 精确 top-k：分块稀疏乘法（无多语言模型时）
            for st in range(0, len(q), 256):
                qq = q[st:st + 256]; M = (self.vec[qq] @ self.vec.T).toarray()
                I = np.argpartition(-M, k - 1, axis=1)[:, :k] if k < n else np.tile(np.arange(n), (len(qq), 1))
                emit(qq, I, np.take_along_axis(M, I, 1))
            return _uniq(ch)
        if self.ann is None:           # 精确 top-k（暴力内积）：多语言模型下同领域标题普遍 ≥0.45，范围检索会退化成全比较
            import faiss
            ix = faiss.IndexFlatIP(self.vec.shape[1]); ix.add(self.vec)
        else: ix = self.ann[1]          # 近似：HNSW top-k
        for st in range(0, len(q), 8192):
            qq = q[st:st + 8192]; D, I = ix.search(self.vec[qq], k); emit(qq, I, D)
        return _uniq(ch)
    def sim(self, i, j):
        import scipy.sparse as sp
        if self.vec is None: return 0.0
        if sp.issparse(self.vec): return float(self.vec[i].multiply(self.vec[j]).sum())
        return float(self.vec[i] @ self.vec[j])
