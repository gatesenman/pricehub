"""v13 去重分块基准：合成 N 个商品（同款簇 + 难负例），对比分块候选与全比较。
用法：python scripts/bench_dedup.py 10000 [--recall] [--st]   输出一行 JSON 到 stdout（并追加到 docs/bench_dedup_v13.jsonl）。
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
def main():
    n = int(sys.argv[1]); recall = "--recall" in sys.argv; st = "--st" in sys.argv
    t = time.time(); ps, gt = gen(n); F = [extract(p) for p in ps]; t_feat = time.time() - t
    t = time.time()
    if st:
        from app import search; V = np.asarray(search._emb([f["norm"] for f in F]), np.float32); vname = "多语言模型"
    else: V = hash_vec(F); vname = "哈希投影64维"
    t_vec = time.time() - t
    t = time.time(); ix = BlockIndex(exact_max=20000, k=30); ix.add(F, V); t_build = time.time() - t
    t = time.time(); codes, stt = ix.candidate_codes(); t_cand = time.time() - t
    cand = set(zip((codes // n).tolist(), (codes % n).tolist())) if n <= 200000 else None
    thr = 0.70; res = {"n": n, "vectors": vname, "ann": "精确 top-k=30" if n <= 20000 else "HNSW k=30", "all_pairs": n * (n - 1) // 2, "candidate_pairs": int(len(codes)),
           "reduction": round(1 - len(codes) / (n * (n - 1) / 2), 6), "by_source": {k: v for k, v in stt.items() if not k.endswith("_new")},
           "t_features_s": round(t_feat, 1), "t_vectors_s": round(t_vec, 1), "t_index_s": round(t_build, 1), "t_candidates_s": round(t_cand, 1)}
    def score(i, j):
        s = float(V[i] @ V[j])
        if s < 0.45 and not (F[i]["ids"] and F[j]["ids"]): return 0
        return S.score_pair(ps[i], ps[j], F[i], F[j], s, "x")[0]
    if n <= 200000:
        from collections import defaultdict
        t = time.time(); hit = {p for p in cand if score(*p) >= thr}; res["t_score_s"] = round(time.time() - t, 1); res["suggestions"] = len(hit)
        gtp = set(); cl = defaultdict(list)
        for i, c in enumerate(gt): cl[c].append(i)
        for m in cl.values():
            for a in range(len(m)):
                for b in range(a + 1, len(m)): gtp.add((m[a], m[b]))
        res["truth_pairs"] = len(gtp); res["truth_recall_candidates"] = round(len(gtp & cand) / max(1, len(gtp)), 4)
        res["truth_recall_suggestions"] = round(len(gtp & hit) / max(1, len(gtp)), 4); res["precision_suggestions"] = round(len(gtp & hit) / max(1, len(hit)), 4)
    if recall:   # 全比较对照：所有对中（语义 ≥0.45 或都有条码）再打分 ≥ thr —— 与 v12 行为一致
        t = time.time(); full = set(); hasid = np.array([bool(f["ids"]) for f in F])
        for a in range(0, n, 1000):
            M = V[a:a + 1000] @ V.T
            for r in range(M.shape[0]):
                i = a + r; js = np.nonzero((M[r] >= 0.45) | (hasid[i] & hasid))[0]
                for j in js[js > i]:
                    if score(i, int(j)) >= thr: full.add((i, int(j)))
        res["t_all_pairs_s"] = round(time.time() - t, 1); res["all_pairs_suggestions"] = len(full)
        res["recall_vs_all_pairs"] = round(len(full & hit) / max(1, len(full)), 4)
        ix2 = BlockIndex(exact_max=0, k=30); ix2.add(F, V); c2, _ = ix2.candidate_pairs(); h2 = {p for p in c2 if score(*p) >= thr}
        res["hnsw_candidate_pairs"] = len(c2); res["hnsw_recall_vs_all_pairs"] = round(len(full & h2) / max(1, len(full)), 4)
        ix3 = BlockIndex(exact_max=0, k=30, use_ann=False); ix3.add(F, None); c3, _ = ix3.candidate_pairs(); h3 = {p for p in c3 if score(*p) >= thr}
        res["keys_lsh_only_recall_vs_all_pairs"] = round(len(full & h3) / max(1, len(full)), 4)
    res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(json.dumps(res, ensure_ascii=False)); open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "bench_dedup_v13.jsonl"), "a").write(json.dumps(res, ensure_ascii=False) + "\n")
def incr():
    n = int(sys.argv[2]); m = int(sys.argv[3]); ps, gt = gen(n + m); F = [extract(p) for p in ps]; V = hash_vec(F)
    t = time.time(); ix = BlockIndex(exact_max=20000, k=30); ix.add(F[:n], V[:n]); t_build = time.time() - t
    t = time.time(); new = ix.add(F[n:], V[n:]); c, stt = ix.candidate_codes(new); t_q = time.time() - t
    res = {"mode": "incremental", "existing": n, "new": m, "t_build_existing_s": round(t_build, 1), "t_add_and_query_new_s": round(t_q, 2), "candidate_pairs_new": int(len(c)),
           "all_pairs_new": m * n + m * (m - 1) // 2}
    print(json.dumps(res, ensure_ascii=False)); open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "bench_dedup_v13.jsonl"), "a").write(json.dumps(res, ensure_ascii=False) + "\n")
incr() if sys.argv[1] == "incr" else main()
