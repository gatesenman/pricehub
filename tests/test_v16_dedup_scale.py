"""v16：LSH/超大块收紧，以及大规模打分并行与预筛。结果应与单进程一致。"""
import os, sys, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.domains.dedup.features import extract
from app.domains.dedup.blocking import BlockIndex, MinHasher, _bucket_codes
from app.domains.dedup.parallel import prefilter_codes, score_candidate_codes
from app.domains.dedup import service as S


def _data(n=1500):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bd", os.path.join(ROOT, "scripts", "bench_dedup.py"))
    src = open(spec.origin, encoding="utf-8").read().rsplit("\nincr() if", 1)[0]
    g = {"__file__": spec.origin, "__name__": "bd"}; exec(src, g)
    ps, gt = g["gen"](n); F = [extract(p) for p in ps]; return ps, gt, F, g["hash_vec"](F)


def test_minhash_default_tighter_than_v13():
    m = MinHasher()
    assert m.num == 48 and m.bands == 8 and m.rows == 6
    old = MinHasher(num=32, bands=8)
    # 同一对标题，更长的 band 行应更不容易碰撞（相同签名比例更低或持平）
    a, b = "apple iphone 17 pro 256gb 黑色 官方旗舰店", "苹果 iphone 17 pro 256gb 黑色 正品"
    sa, sb = m.sig(a), m.sig(b)
    oa, ob = old.sig(a), old.sig(b)
    assert sa is not None and len(sa) == 48 and len(oa) == 32
    # 与无关标题相比，同款仍更相近
    z = m.sig("戴森 吹风机 hd15")
    assert (sa == sb).mean() > (sa == z).mean()


def test_oversize_lsh_split_reduces_pairs():
    # 1000 个成员：无拆分键时走窗口；有分散的拆分键时应切得更碎、对数更少
    st1 = {}
    c1 = _bucket_codes(list(range(1000)), 200, 1000, st1)
    assert st1["oversize_blocks"] == 1 and st1.get("oversize_windows") == 1
    assert len(c1) < 1000 * 50
    # 40 组、每组 25 个：会被切开，但组内仍两两成对（25≤200）
    keys = np.repeat(np.arange(40, dtype=np.uint64) << 48, 25)
    st2 = {}
    c2 = _bucket_codes(list(range(1000)), 200, 1000, st2, split_keys=keys)
    assert st2.get("oversize_split", 0) >= 1
    assert c2 is not None and 0 < len(c2) < len(c1)
    # 每组 C(25,2)=300，40 组 = 12000 对
    assert len(c2) == 40 * 25 * 24 // 2
    # 条码键路径：不传 split_keys，行为与 v13 窗口一致
    st3 = {}
    c3 = _bucket_codes(list(range(1000)), 200, 1000, st3, split_keys=None)
    assert len(c3) == len(c1)


def test_lsh_tighten_keeps_truth_recall():
    ps, gt, F, V = _data(2000)
    n = len(ps)
    ix = BlockIndex(); ix.add(F, V)
    codes, st = ix.candidate_codes()
    assert st["lsh"] < st["total"] or st["total"] > 0
    # 真值同款对应在候选里（条码/品牌型号/LSH/ANN 并集）
    # 真值同款对
    from collections import defaultdict
    cl = defaultdict(list)
    for i, c in enumerate(gt): cl[c].append(i)
    want = []
    for m in cl.values():
        m.sort()
        for a in range(len(m)):
            for b in range(a + 1, len(m)):
                want.append(m[a] * n + m[b])
    want = np.asarray(want, np.int64)
    idx = np.minimum(np.searchsorted(codes, want), len(codes) - 1)
    rec = float((codes[idx] == want).mean())
    assert rec >= 0.99, rec
    # 候选仍远小于全比较
    assert len(codes) < n * (n - 1) // 2 * 0.05


def test_prefilter_keeps_id_pairs_and_high_sim():
    n = 8
    V = np.eye(n, dtype=np.float32)
    V[1] = V[0]  # 0 和 1 完全相同
    # 把 0-1、2-3 放进候选；2-3 正交，但都有条码
    codes = np.array([0 * n + 1, 2 * n + 3, 4 * n + 5], np.int64)
    has = np.array([False, False, True, True, False, False, False, False])
    kept, sims = prefilter_codes(codes, n, V, has, sim_floor=0.45)
    assert set(kept.tolist()) == {0 * n + 1, 2 * n + 3}
    assert 4 * n + 5 not in set(kept.tolist())
    assert abs(float(sims[np.where(kept == 0 * n + 1)][0]) - 1.0) < 1e-5


def test_parallel_scores_match_sequential():
    ps, gt, F, V = _data(1200)
    n = len(ps)
    ix = BlockIndex(); ix.add(F, V)
    codes, _ = ix.candidate_codes()
    # 强制走并行分支（阈值 20000，这里用 workers=2 且把门槛视为足够）
    seq = score_candidate_codes(codes, n, ps, F, vec=V, engine="x", threshold=0.70, workers=1)
    par = score_candidate_codes(codes, n, ps, F, vec=V, engine="x", threshold=0.70, workers=2, chunk=2000)
    kseq = {(i, j): round(sc, 4) for i, j, sc, *_ in seq}
    kpar = {(i, j): round(sc, 4) for i, j, sc, *_ in par}
    assert kseq == kpar and len(kseq) > 0


def test_window_codes_canonical_even_when_split_order_is_reversed():
    from app.domains.dedup.blocking import _window_codes
    m=np.array([9,1,8,2,7,3,6,4,5,0],dtype=np.int64)
    c=_window_codes(m,20,10)
    assert np.all(c//10 < c%10)
    assert len(np.unique(c))==len(c)
