"""v11：pgvector 向量后端——与精确检索一致、按内容复用、出错自动回退；PostgreSQL 下语义搜索走 pgvector。"""
import os, sys, time, subprocess, tempfile, numpy as np, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
PG = os.environ.get("PRICEHUB_TEST_PG", "postgresql://pricehub:pricehub@127.0.0.1:5432/pricehub_test")
def _pg_vector_ok():
    try:
        import psycopg
        with psycopg.connect(PG, connect_timeout=3) as c: return bool(c.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone())
    except Exception: return False
pytestmark = pytest.mark.skipif(not _pg_vector_ok(), reason="本机 PostgreSQL 没有 pgvector")
def _child(code, **env):
    e = dict(os.environ, PRICEHUB_DB_URL=PG, PRICEHUB_PG_SCHEMA=f"t_pgv_{os.getpid()}_{int(time.time()*1000)%100000}", PYTHONPATH=ROOT, PRICEHUB_NO_ST="1", PRICEHUB_NO_CLIP="1", **env)
    r = subprocess.run([PY, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]; return r.stdout.strip().splitlines()[-1]
def test_pgvector_index_matches_exact_and_reuses():
    out = _child("""
import numpy as np, time
from app.core import pgvector_store as pv
from app.core.vector import VectorIndex, FallbackIndex
assert pv.available(force=True)['ok']
r = np.random.default_rng(0); X = r.normal(size=(3000, 64)).astype('float32'); X /= np.linalg.norm(X, axis=1, keepdims=True)
p = pv.PgVectorIndex(ef_search=200).build(X); ex = VectorIndex(kind='numpy').build(X)
hits = 0
for q in X[:50]:
    I, D = p.search(q, 10); E, _ = ex.search(q, 10); hits += len(set(I.tolist()) & set(E.tolist()))
    assert I[0] == E[0] and abs(D[0] - 1.0) < 1e-3
t = time.time(); p2 = pv.PgVectorIndex().build(X); reuse = time.time() - t
assert p2.tbl == p.tbl
# 主引擎坏了自动回退
import os; os.environ['PRICEHUB_VECTOR_ENGINE'] = 'pgvector'
f = FallbackIndex(len(X)).build(X); assert f.engine == 'pgvector-hnsw'
f.primary.tbl = 'pv_not_exists'; I, D = f.search(X[5], 3); assert I[0] == 5 and f.fallback is not None and f.engine != 'pgvector-hnsw'
print(hits / 500, round(reuse, 3))
""")
    recall, reuse = map(float, out.split()); assert recall >= 0.95 and reuse < 2
def test_auto_selection_threshold():
    out = _child("""
from app.core.vector import make_index
import os
a = make_index(100).__class__.__name__; os.environ['PRICEHUB_HNSW_MIN'] = '50'
import importlib, app.core.vector as V; importlib.reload(V); b = V.make_index(100).__class__.__name__
os.environ['PRICEHUB_VECTOR_ENGINE'] = 'faiss'; c = V.make_index(10**6).__class__.__name__
print(a, b, c)
""")
    assert out.split() == ["VectorIndex", "PgVectorIndex", "VectorIndex"]
def test_sqlite_backend_reports_unavailable():
    e = dict(os.environ, PYTHONPATH=ROOT, PRICEHUB_NO_ST="1", PRICEHUB_NO_CLIP="1", PRICEHUB_DB=tempfile.mktemp(suffix=".db")); e.pop("PRICEHUB_DB_URL", None)
    r = subprocess.run([PY, "-c", "from app.core import pgvector_store as pv; s=pv.status(); print(s['ok'], 'SQLite' in s['reason'])"], cwd=ROOT, env=e, capture_output=True, text=True, timeout=300)
    assert r.stdout.strip().splitlines()[-1] == "False True", r.stderr[-2000:]
def test_semantic_search_api_uses_pgvector():
    from test_v10_nodes import _wait
    e = dict(os.environ, PRICEHUB_DB_URL=PG, PRICEHUB_PG_SCHEMA=f"t_pgvapi_{os.getpid()}", PRICEHUB_VECTOR_ENGINE="pgvector", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PYTHONPATH=ROOT,
             PRICEHUB_DB=tempfile.mktemp(suffix=".db"))
    e["PRICEHUB_EMB_CACHE"] = e["PRICEHUB_DB"] + ".npz"
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8986"], cwd=ROOT, env=e)
    try:
        _wait("http://127.0.0.1:8986/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8986", timeout=180)
        for i, t in enumerate(["Apple iPhone 17 Pro 256GB 手机", "索尼 WH-1000XM6 降噪耳机", "戴森 V15 吸尘器", "Nintendo Switch 2 游戏机"]):
            c.post("/api/products", json={"title": t, "platform": "test", "external_id": f"pgv{i}"})
        r = c.get("/api/search/semantic", params={"q": "noise cancelling headphones", "k": 3}).json()
        assert r["vector_engine"] == "pgvector-hnsw" and "WH-1000XM6" in r["results"][0]["title"]
        assert c.get("/api/pgvector/status").json()["ok"] is True
    finally: p.terminate()
