"""pgvector vs FAISS vs numpy 精确：召回率@10 与单条查询延迟。
用法：PRICEHUB_DB_URL=postgresql://... python scripts/bench_vector.py [N ...]
数据：384 维（与文本模型同维）、按 1000 个簇生成的归一化向量（比纯随机更接近真实商品分布）；真值 = numpy 精确检索。"""
import os, sys, time, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PRICEHUB_PG_SCHEMA", "bench_vec")
from app.core.vector import VectorIndex, HAVE_FAISS
from app.core import pgvector_store as pv
def data(n, d=384, q=200, seed=0):
    r = np.random.default_rng(seed); C = r.normal(size=(1000, d)).astype(np.float32)
    X = C[r.integers(0, 1000, n)] + 2.0 * r.normal(size=(n, d)).astype(np.float32); X /= np.linalg.norm(X, axis=1, keepdims=True)
    Q = C[r.integers(0, 1000, q)] + 2.0 * r.normal(size=(q, d)).astype(np.float32); Q /= np.linalg.norm(Q, axis=1, keepdims=True)
    return X.astype(np.float32), Q.astype(np.float32)
def run(name, idx, Q, truth, k=10):
    lat, rec = [], []
    for i, q in enumerate(Q):
        t = time.perf_counter(); I, _ = idx.search(q, k); lat.append((time.perf_counter() - t) * 1000)
        rec.append(len(set(I.tolist()) & truth[i]) / k)
    lat = np.array(lat); return {"engine": name, "recall@10": round(float(np.mean(rec)), 4), "p50_ms": round(float(np.percentile(lat, 50)), 2), "p95_ms": round(float(np.percentile(lat, 95)), 2)}
def main(ns):
    out = []
    for n in ns:
        X, Q = data(n); exact = VectorIndex(kind="numpy").build(X)
        truth = [set(exact.search(q, 10)[0].tolist()) for q in Q]
        row = {"n": n, "results": [run("numpy-exact", exact, Q, truth)]}
        if HAVE_FAISS:
            t = time.perf_counter(); f = VectorIndex(kind="hnsw").build(X); bt = time.perf_counter() - t
            r = run("faiss-hnsw(M32,ef64)", f, Q, truth); r["build_s"] = round(bt, 2); row["results"].append(r)
        st = pv.available(force=True)
        if st.get("ok"):
            t = time.perf_counter(); p = pv.PgVectorIndex(ef_search=40).build(X); bt = time.perf_counter() - t
            for ef in (40, 100, 200):
                p.ef_search = ef; r = run(f"pgvector-hnsw(m16,ef{ef})", p, Q, truth); r["build_s"] = round(bt, 2) if ef == 40 else None; row["results"].append(r)
            t = time.perf_counter(); pv.PgVectorIndex().build(X); row["pgvector_reuse_s"] = round(time.perf_counter() - t, 2)
        else: row["pgvector"] = st
        print(json.dumps(row, ensure_ascii=False), flush=True); out.append(row)
    return out
if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [10000, 50000])
