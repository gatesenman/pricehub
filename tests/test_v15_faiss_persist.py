"""v15：FAISS / numpy 向量索引落盘——签名失效、启动加载、增删更新、冷启动对比。"""
import os, sys, time, json, tempfile, shutil, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.core.vector import (
    VectorIndex, HAVE_FAISS, content_digest, make_signature, signatures_match,
    save_vector_index, load_vector_index, remove_index, index_paths, FORMAT,
)

def _rng_vecs(n, dim=64, seed=0):
    r = np.random.RandomState(seed)
    X = r.randn(n, dim).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    return X

def _sig(name, n, dim, digest, kind="auto"):
    return make_signature(name=name, kind=kind, dim=dim, n=n, digest=digest)


def test_faiss_must_be_available():
    assert HAVE_FAISS, "本轮测试需要 faiss-cpu"


def test_save_load_numpy_and_search_identical(tmp_path=None):
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        X = _rng_vecs(200, 32)
        ids = np.arange(200, dtype=np.int64)
        vi = VectorIndex(kind="numpy").build(X, ids)
        I0, D0 = vi.search(X[7], 5)
        digest = content_digest(ids, vectors=X)
        meta = save_vector_index(vi, "t_numpy", _sig("t_numpy", 200, 32, digest, "numpy-exact"), root=root)
        assert meta["kind"] == "numpy-exact" and os.path.exists(index_paths("t_numpy", root)["snapshot"])
        loaded, reason = load_vector_index("t_numpy", expected=_sig("t_numpy", 200, 32, digest), root=root)
        assert reason == "ok" and loaded is not None and loaded.engine == "numpy-exact"
        I1, D1 = loaded.search(X[7], 5)
        assert np.array_equal(I0, I1) and np.allclose(D0, D1, atol=1e-5)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_load_hnsw_and_flat(tmp_path=None):
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        X = _rng_vecs(300, 64, seed=2)
        ids = np.arange(300, dtype=np.int64) + 1000
        digest = content_digest(ids, texts=[f"doc-{i}" for i in ids])
        for kind in ("hnsw", "flat"):
            vi = VectorIndex(kind=kind).build(X, ids, keep_vectors=True)
            name = f"t_{kind}"
            save_vector_index(vi, name, _sig(name, 300, 64, digest, vi.engine), root=root, store_vectors=True)
            loaded, reason = load_vector_index(name, expected=_sig(name, 300, 64, digest), root=root)
            assert reason == "ok", (kind, reason)
            assert loaded.engine == vi.engine and loaded.size() == 300
            I0, D0 = vi.search(X[11], 8)
            I1, D1 = loaded.search(X[11], 8)
            # Flat 应完全一致；HNSW 对 300 条、ef=64 也应顶到精确结果
            assert np.array_equal(I0, I1), (kind, I0, I1)
            assert np.allclose(D0, D1, atol=1e-5)
            assert loaded.M is not None and loaded.M.shape == X.shape
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_signature_mismatch_and_corrupt_do_not_load():
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        X = _rng_vecs(80, 16)
        ids = np.arange(80, dtype=np.int64)
        digest = content_digest(ids, texts=[f"t{i}" for i in ids])
        vi = VectorIndex(kind="numpy").build(X, ids)
        save_vector_index(vi, "t_sig", _sig("t_sig", 80, 16, digest), root=root)
        # 文本变了
        digest2 = content_digest(ids, texts=[f"t{i}-x" for i in ids])
        loaded, reason = load_vector_index("t_sig", expected=_sig("t_sig", 80, 16, digest2), root=root)
        assert loaded is None and reason == "mismatch"
        # 数量变了
        loaded, reason = load_vector_index("t_sig", expected=_sig("t_sig", 81, 16, digest), root=root)
        assert loaded is None and reason == "mismatch"
        # 损坏 meta
        open(index_paths("t_sig", root)["snapshot"], "w").write("{not json")
        loaded, reason = load_vector_index("t_sig", expected=_sig("t_sig", 80, 16, digest), root=root)
        assert loaded is None and reason == "corrupt"
        # 缺失
        remove_index("t_sig", root=root)
        loaded, reason = load_vector_index("t_sig", expected=_sig("t_sig", 80, 16, digest), root=root)
        assert loaded is None and reason == "missing"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_incremental_add_persists_and_is_searchable():
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        X = _rng_vecs(50, 32, seed=5)
        ids = np.arange(50, dtype=np.int64)
        vi = VectorIndex(kind="flat").build(X, ids, keep_vectors=True)
        digest = content_digest(ids, vectors=X)
        save_vector_index(vi, "t_add", _sig("t_add", 50, 32, digest), root=root, store_vectors=True)
        nxt = _rng_vecs(3, 32, seed=9)
        vi.add(nxt, np.arange(50, 53, dtype=np.int64))
        allX = np.vstack([X, nxt])
        digest2 = content_digest(vi.ids, vectors=allX)
        save_vector_index(vi, "t_add", _sig("t_add", 53, 32, digest2), root=root, store_vectors=True)
        # 旧签名不能用
        loaded, reason = load_vector_index("t_add", expected=_sig("t_add", 50, 32, digest), root=root)
        assert loaded is None and reason == "mismatch"
        loaded, reason = load_vector_index("t_add", expected=_sig("t_add", 53, 32, digest2), root=root)
        assert reason == "ok" and loaded.size() == 53
        I, D = loaded.search(nxt[1], 3)
        assert int(I[0]) == 51
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_delete_invalidates_stale_file():
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        X = _rng_vecs(40, 16, seed=3)
        vi = VectorIndex(kind="numpy").build(X)
        digest = content_digest(vi.ids, vectors=X)
        save_vector_index(vi, "t_del", _sig("t_del", 40, 16, digest), root=root)
        remove_index("t_del", root=root)
        loaded, reason = load_vector_index("t_del", expected=_sig("t_del", 40, 16, digest), root=root)
        assert loaded is None and reason == "missing"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_search_index_load_and_product_hooks(monkeypatch=None):
    """semantic 层：内容签名缓存、磁盘加载、删除失效、改标题失效。不依赖 ST（自行提供向量）。"""
    import app.domains.search.semantic as S
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    old_dir = os.environ.get("PRICEHUB_FAISS_DIR")
    os.environ["PRICEHUB_FAISS_DIR"] = root
    # 重置模块级缓存
    S._INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
    products = [{"id": i + 1, "title": f"Apple iPhone {i}", "category": "phone", "brand": "Apple"} for i in range(30)]
    X = _rng_vecs(30, 32, seed=11)
    cache = {S.doc(p): X[i] for i, p in enumerate(products)}
    def fake_emb(texts):
        return np.array([cache[t] if t in cache else _rng_vecs(1, 32, seed=abs(hash(t)) % 10000)[0] for t in texts])
    orig_emb, orig_st = S._emb, S._ST
    try:
        S._emb = fake_emb
        S._ST = object()  # 让 on_products_changed 走增量
        M = S.index_matrix(products)
        assert M.shape == (30, 32) and S._INDEX["source"].startswith("build")
        # 二次调用应走内存
        S.index_matrix(products)
        assert S._INDEX["source"].startswith("build")
        # 模拟进程重启：清内存，再加载
        src = S._INDEX["source"]
        S._INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
        M2 = S.index_matrix(products)
        assert S._INDEX["source"] == "disk"
        assert np.allclose(M, M2)
        I, D = S._INDEX["vi"].search(X[4], 3)
        assert int(I[0]) == 4
        # 删除商品：失效，旧文件不再被使用
        r = S.on_products_changed(removed=[products[0]["id"]])
        assert r["action"] == "invalidate"
        loaded, reason = load_vector_index("search", root=root)
        # remove_index 后即使无 expected 也 missing（或 corrupt if meta gone）
        assert reason == "missing"
        # 改标题同样失效（先重建再改）
        S._emb = fake_emb
        S._ST = object()
        S.index_matrix(products)
        r = S.on_products_changed(updated=[2])
        assert r["action"] == "invalidate"
        # 新增：内存有索引时增量
        S._emb = fake_emb
        S._ST = object()
        S.index_matrix(products)
        newp = {"id": 99, "title": "Sony WH-1000XM5", "category": "audio", "brand": "Sony"}
        cache[S.doc(newp)] = _rng_vecs(1, 32, seed=21)[0]
        r = S.on_products_changed(added=[newp], products=products + [newp])
        assert r["action"] == "add" and r["n"] == 31
        S._INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
        S.index_matrix(products + [newp])
        assert S._INDEX["source"] == "disk" and S._INDEX["vi"].size() == 31
        # 启动加载路径
        S._INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
        wr = S.warm_persisted_index(products + [newp])
        assert wr["action"] == "load" and wr["n"] == 31
        wr2 = S.warm_persisted_index(products)  # 少一个，签名不符
        assert wr2["action"] == "miss" and wr2["reason"] == "mismatch"
    finally:
        S._emb, S._ST = orig_emb, orig_st
        S._INDEX.update(key=None, M=None, vi=None, source=None, sig=None)
        if old_dir is None: os.environ.pop("PRICEHUB_FAISS_DIR", None)
        else: os.environ["PRICEHUB_FAISS_DIR"] = old_dir
        shutil.rmtree(root, ignore_errors=True)


def test_dedup_ann_save_load_keeps_neighbors():
    from app.domains.dedup.blocking import BlockIndex
    from app.domains.dedup.features import extract
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        ps = [{"title": "Apple iPhone 17 Pro 256GB 黑色", "brand": "Apple", "model": "iPhone 17 Pro", "gtin": None},
              {"title": "苹果 iPhone 17 Pro 256GB 黑色", "brand": "Apple", "model": "iPhone 17 Pro", "gtin": None},
              {"title": "戴森 吹风机 HD15", "brand": "Dyson", "model": "HD15", "gtin": None},
              {"title": "Sony WH-1000XM5 银色", "brand": "Sony", "model": "WH-1000XM5", "gtin": None}]
        # 再加一些填充，便于 HNSW/flat
        for i in range(20):
            ps.append({"title": f"填充商品 {i} 型号 Z{i}", "brand": "Fill", "model": f"Z{i}", "gtin": None})
        F = [extract(p) for p in ps]
        X = _rng_vecs(len(ps), 32, seed=4)
        # 让 0/1 很近，其它随机
        X[1] = X[0] + 0.01 * X[1]; X[1] /= np.linalg.norm(X[1]) + 1e-9
        ix = BlockIndex(exact_max=1000, k=5); ix.add(F, X)
        codes, st = ix.candidate_codes()
        digest = content_digest(range(len(ps)), texts=[p["title"] for p in ps])
        sig = _sig("dedup", len(ps), 32, digest)
        ix.save_ann("dedup", signature=sig, root=root)
        ix2 = BlockIndex(exact_max=1000, k=5); ix2.add(F, None)
        ok, reason = ix2.load_ann("dedup", expected=sig, root=root)
        assert ok and reason == "ok" and ix2.vec is not None
        codes2, st2 = ix2.candidate_codes()
        assert set(codes.tolist()) == set(codes2.tolist())
        assert abs(ix.sim(0, 1) - ix2.sim(0, 1)) < 1e-5
        # 标题变了不能加载
        bad = _sig("dedup", len(ps), 32, content_digest(range(len(ps)), texts=["x"] * len(ps)))
        ix3 = BlockIndex(exact_max=1000, k=5); ix3.add(F, None)
        ok, reason = ix3.load_ann("dedup", expected=bad, root=root)
        assert not ok and reason == "mismatch"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cold_start_load_faster_than_rebuild():
    """强制 HNSW：对比从向量重建与从磁盘加载。数字写入返回值供文档引用。"""
    root = tempfile.mkdtemp(prefix="ph-faiss-")
    try:
        n, dim = 20000, 64
        X = _rng_vecs(n, dim, seed=17)
        ids = np.arange(n, dtype=np.int64)
        digest = content_digest(ids, vectors=X)
        t0 = time.perf_counter()
        vi = VectorIndex(kind="hnsw").build(X, ids)
        t_build = time.perf_counter() - t0
        t0 = time.perf_counter()
        save_vector_index(vi, "bench", _sig("bench", n, dim, digest, "faiss-hnsw"), root=root)
        t_save = time.perf_counter() - t0
        t0 = time.perf_counter()
        loaded, reason = load_vector_index("bench", expected=_sig("bench", n, dim, digest), root=root)
        t_load = time.perf_counter() - t0
        assert reason == "ok" and loaded is not None
        q = X[123]
        I0, D0 = vi.search(q, 10)
        I1, D1 = loaded.search(q, 10)
        assert np.array_equal(I0, I1) and np.allclose(D0, D1, atol=1e-5)
        # 加载应明显快于重建（允许机器抖动，至少快 2 倍；同时记录绝对时间）
        print(f"COLD_START n={n} dim={dim} build={t_build:.4f}s save={t_save:.4f}s load={t_load:.4f}s speedup={t_build/max(t_load,1e-6):.1f}x", flush=True)
        assert t_load < t_build / 2, f"load {t_load:.4f}s not faster than half of build {t_build:.4f}s"
        # 写入一个旁路文件，供进度文档摘录（测试目录，不入库）
        out = os.path.join(root, "cold_start.json")
        json.dump({"n": n, "dim": dim, "t_build_s": round(t_build, 4), "t_save_s": round(t_save, 4),
                   "t_load_s": round(t_load, 4), "speedup": round(t_build / max(t_load, 1e-6), 2)}, open(out, "w"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_signatures_match_auto_kind():
    stored = make_signature(name="search", kind="faiss-hnsw", dim=64, n=10, digest="abc", params={"efSearch": 64})
    expected = make_signature(name="search", kind="auto", dim=0, n=10, digest="abc")
    assert signatures_match(stored, expected)
    expected2 = make_signature(name="search", kind="auto", dim=0, n=10, digest="zzz")
    assert not signatures_match(stored, expected2)
    assert stored["format"] == FORMAT
