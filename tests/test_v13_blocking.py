"""v13：去重分块（条码键 / 品牌+型号键 / MinHash-LSH / 向量 top-k）——与全比较结果一致、增量只查新商品、超大块截断、开关回退全比较。"""
import os, sys, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "scripts"))
from app.domains.dedup.features import extract
from app.domains.dedup.blocking import BlockIndex, MinHasher, _bucket_codes
from app.domains.dedup import service as S

def _data(n=1500):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bd", os.path.join(ROOT, "scripts", "bench_dedup.py"))
    src = open(spec.origin, encoding="utf-8").read().rsplit("\nincr() if", 1)[0]; g = {"__file__": spec.origin, "__name__": "bd"}; exec(src, g)
    ps, gt = g["gen"](n); F = [extract(p) for p in ps]; return ps, gt, F, g["hash_vec"](F)

def _suggest(ps, F, V, pairs, thr=0.70):
    out = set()
    for i, j in pairs:
        s = float(V[i] @ V[j])
        if s < 0.45 and not (F[i]["ids"] and F[j]["ids"]): continue
        if S.score_pair(ps[i], ps[j], F[i], F[j], s, "x")[0] >= thr: out.add((i, j))
    return out

def test_blocking_matches_all_pairs():
    ps, gt, F, V = _data(); n = len(ps)
    ix = BlockIndex(); ix.add(F, V); cand, st = ix.candidate_pairs()
    assert st["total"] == len(cand) < n * (n - 1) // 2 * 0.05            # 候选对 < 全比较的 5%
    full = _suggest(ps, F, V, ((i, j) for i in range(n) for j in range(i + 1, n)))
    got = _suggest(ps, F, V, cand)
    assert full and got == full                                            # 建议与全比较完全一致（召回 100%）
    hn = BlockIndex(exact_max=0); hn.add(F, V); c2, _ = hn.candidate_pairs(); assert len(_suggest(ps, F, V, c2) & full) / len(full) >= 0.99

def test_incremental_only_new():
    ps, gt, F, V = _data(1200); n0 = 1000
    full = BlockIndex(); full.add(F, V); allc, _ = full.candidate_pairs()
    ix = BlockIndex(); ix.add(F[:n0], V[:n0]); new = ix.add(F[n0:], V[n0:]); c, st = ix.candidate_pairs(new)
    assert new == list(range(n0, 1200)) and all(i >= n0 or j >= n0 for i, j in c)
    want = {p for p in _suggest(ps, F, V, allc) if p[0] >= n0 or p[1] >= n0}
    got = _suggest(ps, F, V, c); assert want and len(got & want) / len(want) >= 0.99   # 含新商品的建议与全量重建一致
    assert len(c) < 200 * (n0 + 200) * 0.05

def test_oversize_block_window_and_minhash():
    st = {}; c = _bucket_codes(list(range(1000)), 200, 1000, st); assert st["oversize_blocks"] == 1 and len(c) < 1000 * 50
    small = _bucket_codes([5, 3, 9], 200, 10, {}); assert sorted(small.tolist()) == [3 * 10 + 5, 3 * 10 + 9, 5 * 10 + 9]
    m = MinHasher(); a, b, z = m.sig("apple iphone 17 pro 256gb 黑色"), m.sig("苹果 iphone 17 pro 256gb 黑色"), m.sig("戴森 吹风机 hd15")
    assert (a == b).mean() > (a == z).mean()

def test_gtin_key_blocks_different_titles():
    ps = [{"title": "完全不同的标题 A", "gtin": "4901234567894"}, {"title": "xyz 另一个", "gtin": "04901234567894"}, {"title": "无关商品 C"}]
    F = [extract(p) for p in ps]; ix = BlockIndex(use_ann=False, use_lsh=False); ix.add(F); c, st = ix.candidate_pairs(); assert c == {(0, 1)}

def test_settings_roundtrip_defaults():
    assert S.DEFAULTS["blocking"]["enabled"] is True and S.DEFAULTS["blocking"]["ann_k"] == 30
