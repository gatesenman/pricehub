"""v13 去重分块基准：合成 N 个商品（同款簇 + 难负例），对比分块候选与全比较。
用法：python scripts/bench_dedup.py 10000 [--recall] [--st] [--workers N]   输出一行 JSON 到 stdout（并追加到 docs/bench_dedup_v16.jsonl）。
v16：打分走分块预筛 + 可选多进程；100 万规模不再因 Python 元组集合而跳过打分。
向量：默认用"字符 3-gram 哈希 → 64 维随机投影"的轻量向量（1M 规模时多语言模型编码太慢）；--st 用真实多语言模型（仅建议 ≤10k）。
"""
import sys, os, time, json, random, resource, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app.domains.dedup.features import extract
from app.domains.dedup.blocking import BlockIndex
from app.domains.dedup import service as S

BR = {"apple": ["Apple", "苹果", "蘋果"], "samsung": ["Samsung", "三星"], "xiaomi": ["Xiaomi", "小米"], "sony": ["Sony", "索尼"], "huawei": ["Huawei", "华为"],
      "lenovo": ["Lenovo", "联想"], "dell": ["Dell", "戴尔"], "asus": ["ASUS", "华硕"], "logitech": ["Logitech", "罗技"], "anker": ["Anker", "安克"]}
KIND = ["手机", "耳机", "笔记本电脑", "平板", "显示器", "充电器", "鼠标", "键盘", "移动电源", "相机"]
CAP = ["64GB", "128GB", "256GB", "512GB", "1TB", "20000mAh", "65W", "27英寸"]
COL = [["黑色", "Black"], ["白色", "White"], ["银色", "Silver"], ["蓝色", "Blue"], ["灰色", "Gray"]]
NOISE = ["官方旗舰店", "正品", "包邮", "现货", "国行", "限时特价", "新品", "台灣公司貨", "", "", ""]
PLAT = ["jd", "taobao", "pchome", "amazon", "momo", "shopee"]
def gen(n, seed=1):
    r = random.Random(seed); ps, gt = [], []; cid = 0
    while len(ps) < n:
        b = r.choice(list(BR)); model = f"{b[:2].upper()}-{r.choice('ABCDXZ')}{r.randint(100, 99999)}"; kind = r.choice(KIND)
        cap = r.choice(CAP); col = r.choice(COL); gtin = str(r.randint(10**12, 10**13 - 1)) if r.random() < 0.25 else None
        for v in range(r.choice([1, 1, 2, 2, 3, 4])):
            words = [r.choice(BR[b]), kind, model if r.random() < 0.9 else model.replace("-", ""), cap, r.choice(col), r.choice(NOISE)]
            if r.random() < 0.3: words[1], words[3] = words[3], words[1]
            ps.append({"id": len(ps) + 1, "platform": r.choice(PLAT), "title": " ".join(w for w in words if w), "brand": None, "model": None,
                       "gtin": gtin if gtin and r.random() < 0.7 else None, "attrs": {}, "clip": None, "phash": None}); gt.append(cid)
        if r.random() < 0.3 and len(ps) < n:   # 难负例：同型号不同容量
            c2 = r.choice([c for c in CAP if c != cap]); cid += 1
            ps.append({"id": len(ps) + 1, "platform": r.choice(PLAT), "title": f"{r.choice(BR[b])} {kind} {model} {c2} {r.choice(col)}", "brand": None, "model": None,
                       "gtin": None, "attrs": {}, "clip": None, "phash": None}); gt.append(cid)
        cid += 1
    return ps[:n], gt[:n]
def hash_vec(F, dim=64, seed=3):
    """字符 3-gram 特征哈希（2^18）→ 随机 ±1 投影到 dim 维 → L2 归一化。"""
    rng = np.random.RandomState(seed); D = 1 << 18
    R = rng.choice([-1.0, 1.0], size=(D, dim)).astype(np.float32)
    V = np.zeros((len(F), dim), np.float32)
    for i, f in enumerate(F):
        s = (f["norm"] or "").replace(" ", ""); g = [zlib.crc32(s[k:k + 3].encode()) & (D - 1) for k in range(max(1, len(s) - 2))]
        V[i] = R[g].sum(0)
    V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9; return V

def _workers_arg():
    if "--workers" in sys.argv:
        return int(sys.argv[sys.argv.index("--workers") + 1])
    env = os.environ.get("PRICEHUB_SCORE_WORKERS")
    return int(env) if env and env.isdigit() else None

def truth_codes(gt, n):
    from collections import defaultdict
    cl = defaultdict(list)
    for i, c in enumerate(gt): cl[c].append(i)
    out = []
    for m in cl.values():
        m.sort()
        for a in range(len(m)):
            for b in range(a + 1, len(m)):
                out.append(m[a] * n + m[b])
    return np.asarray(out, dtype=np.int64)

def recall_of(codes, want):
    if len(want) == 0: return 1.0
    if len(codes) == 0: return 0.0
    idx = np.searchsorted(codes, want)
    idx = np.minimum(idx, len(codes) - 1)
    return float((codes[idx] == want).mean())

def _append(res):
    line = json.dumps(res, ensure_ascii=False)
    print(line)
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
    open(os.path.join(docs, "bench_dedup_v16.jsonl"), "a").write(line + "\n")

def main():
    n = int(sys.argv[1]); recall = "--recall" in sys.argv; st = "--st" in sys.argv; workers = _workers_arg()
    t = time.time(); ps, gt = gen(n); F = [extract(p) for p in ps]; t_feat = time.time() - t
    t = time.time()
    if st:
        from app import search; V = np.asarray(search._emb([f["norm"] for f in F]), np.float32); vname = "多语言模型"
    else: V = hash_vec(F); vname = "哈希投影64维"
    t_vec = time.time() - t
    t = time.time(); ix = BlockIndex(exact_max=20000, k=30); ix.add(F, V); t_build = time.time() - t
    t = time.time(); codes, stt = ix.candidate_codes(); t_cand = time.time() - t
    thr = 0.70; res = {"n": n, "vectors": vname, "ann": "精确 top-k=30" if n <= 20000 else "HNSW k=30", "all_pairs": n * (n - 1) // 2, "candidate_pairs": int(len(codes)),
           "reduction": round(1 - len(codes) / (n * (n - 1) / 2), 6), "by_source": {k: v for k, v in stt.items() if not k.endswith("_new")},
           "t_features_s": round(t_feat, 1), "t_vectors_s": round(t_vec, 1), "t_index_s": round(t_build, 1), "t_candidates_s": round(t_cand, 1)}
    from app.domains.dedup.parallel import score_candidate_codes, default_workers
    w = workers if workers is not None else default_workers(int(len(codes)))
    res["score_workers"] = w
    t = time.time()
    hits = score_candidate_codes(codes, n, ps, F, vec=V, engine="x", threshold=thr, workers=w)
    res["t_score_s"] = round(time.time() - t, 1); res["suggestions"] = len(hits)
    want = truth_codes(gt, n)
    res["truth_pairs"] = int(len(want))
    res["truth_recall_candidates"] = round(recall_of(codes, want), 4)
    hit_codes = np.sort(np.asarray([i * n + j if i < j else j * n + i for i, j, *_ in hits], dtype=np.int64)) if hits else np.zeros(0, np.int64)
    res["truth_recall_suggestions"] = round(recall_of(hit_codes, want), 4)
    res["precision_suggestions"] = round(float(np.intersect1d(hit_codes, want).size / max(1, len(hit_codes))), 4)
    if recall:   # 全比较对照：所有对中（语义 ≥0.45 或都有条码）再打分 ≥ thr —— 与 v12 行为一致
        def score(i, j):
            s = float(V[i] @ V[j])
            if s < 0.45 and not (F[i]["ids"] and F[j]["ids"]): return 0
            return S.score_pair(ps[i], ps[j], F[i], F[j], s, "x")[0]
        t = time.time(); full = set(); hasid = np.array([bool(f["ids"]) for f in F])
        for a in range(0, n, 1000):
            M = V[a:a + 1000] @ V.T
            for r in range(M.shape[0]):
                i = a + r; js = np.nonzero((M[r] >= 0.45) | (hasid[i] & hasid))[0]
                for j in js[js > i]:
                    if score(i, int(j)) >= thr: full.add((i, int(j)))
        res["t_all_pairs_s"] = round(time.time() - t, 1); res["all_pairs_suggestions"] = len(full)
        hit = {(i, j) for i, j, *_ in hits}
        res["recall_vs_all_pairs"] = round(len(full & hit) / max(1, len(full)), 4)
        ix2 = BlockIndex(exact_max=0, k=30); ix2.add(F, V); c2, _ = ix2.candidate_pairs(); h2 = {p for p in c2 if score(*p) >= thr}
        res["hnsw_candidate_pairs"] = len(c2); res["hnsw_recall_vs_all_pairs"] = round(len(full & h2) / max(1, len(full)), 4)
        ix3 = BlockIndex(exact_max=0, k=30, use_ann=False); ix3.add(F, None); c3, _ = ix3.candidate_pairs(); h3 = {p for p in c3 if score(*p) >= thr}
        res["keys_lsh_only_recall_vs_all_pairs"] = round(len(full & h3) / max(1, len(full)), 4)
    res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    _append(res)
def incr():
    n = int(sys.argv[2]); m = int(sys.argv[3]); ps, gt = gen(n + m); F = [extract(p) for p in ps]; V = hash_vec(F)
    t = time.time(); ix = BlockIndex(exact_max=20000, k=30); ix.add(F[:n], V[:n]); t_build = time.time() - t
    t = time.time(); new = ix.add(F[n:], V[n:]); c, stt = ix.candidate_codes(new); t_q = time.time() - t
    res = {"mode": "incremental", "existing": n, "new": m, "t_build_existing_s": round(t_build, 1), "t_add_and_query_new_s": round(t_q, 2), "candidate_pairs_new": int(len(c)),
           "all_pairs_new": m * n + m * (m - 1) // 2,
           "by_source": {k: v for k, v in stt.items() if not k.endswith("_new")}}
    res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    _append(res)
incr() if sys.argv[1] == "incr" else main()
