"""PostgreSQL pgvector 向量后端（v11 正式接入）。

- 何时使用：PostgreSQL 后端 + 已安装 vector 扩展。PRICEHUB_VECTOR_ENGINE=auto（默认）时，商品数 ≥ PRICEHUB_HNSW_MIN（默认 5 万）
  才走 pgvector（小规模时进程内 numpy 精确检索更快）；=pgvector 强制使用；=faiss / numpy 禁用。PRICEHUB_PGVECTOR=0 可彻底关闭。
- 任何错误都回退到 FAISS / numpy，不影响搜索可用性。
- 扩展装在 public schema，多租户测试用独立 schema，所以类型和运算符一律写成 public.vector / OPERATOR(public.<=>)。
- 用独立连接（autocommit），不占用业务连接的事务。
"""
from __future__ import annotations
import os, io, time, hashlib, logging, threading
import numpy as np
log = logging.getLogger("pricehub.pgvector")
ENABLED = os.environ.get("PRICEHUB_PGVECTOR", "auto") != "0"
_STATE = {"ok": None, "error": None, "version": None, "checked": 0}
_TL = threading.local()
def _conn():
    c = getattr(_TL, "c", None)
    if c is None or c.closed:
        import psycopg
        from app.core import storage as db
        c = psycopg.connect(db.DB_URL, autocommit=True)
        sch = db._pg_schema()
        with c.cursor() as cur:
            if sch != "public": cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{sch}"')
            cur.execute(f'SET search_path TO "{sch}"')
        _TL.c = c
    return c
def available(force=False):
    if not ENABLED: return {"ok": False, "reason": "已关闭（PRICEHUB_PGVECTOR=0）"}
    if not force and _STATE["ok"] is not None and time.time() - _STATE["checked"] < 300:
        return {"ok": True, "version": _STATE["version"]} if _STATE["ok"] else {"ok": False, "reason": _STATE["error"]}
    _STATE["checked"] = time.time()
    try:
        from app.core import storage as db
        if db.BACKEND != "postgresql":
            _STATE.update(ok=False, error="当前不是 PostgreSQL 后端（SQLite 下使用 FAISS / numpy）")
        else:
            with _conn().cursor() as cur:
                cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'"); r = cur.fetchone()
                if not r and os.environ.get("PRICEHUB_PGVECTOR") == "1":
                    try: cur.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"); cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'"); r = cur.fetchone()
                    except Exception as e: log.info("create extension failed", extra={"error": str(e)})
            if r: _STATE.update(ok=True, error=None, version=r[0])
            else: _STATE.update(ok=False, error="数据库没有安装 vector 扩展（需 DBA 执行 CREATE EXTENSION vector）")
    except Exception as e:
        _STATE.update(ok=False, error=str(e))
    return available()
def _lit(v): return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v, dtype=np.float32).ravel()) + "]"
class PgVectorIndex:
    """与 VectorIndex 相同接口：build(M) / search(q, k) -> (位置, 分数)。
    同一组向量（按内容哈希）只写一次，进程重启或多个 API 进程之间直接复用，不必各自在内存里建索引。"""
    engine = "pgvector-hnsw"
    def __init__(self, ef_search=None):
        self.key = None; self.n = 0; self.dim = None; self.ef_search = int(ef_search or os.environ.get("PRICEHUB_PGV_EF", "100"))
    def build(self, vectors, ids=None):
        X = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32)); self.n, self.dim = len(X), (X.shape[1] if len(X) else 0)
        self.key = hashlib.md5(X.tobytes()).hexdigest()[:16]; self.tbl = tbl = f"pv_{self.key}"   # 每组向量一张表、一个 HNSW 索引（避免带过滤的近似检索退化）
        c = _conn()
        with c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS pv_meta(idx_key TEXT PRIMARY KEY, dim INT, n INT, built TIMESTAMPTZ DEFAULT now(), used TIMESTAMPTZ DEFAULT now())")
            cur.execute("SELECT n FROM pv_meta WHERE idx_key=%s", (self.key,)); r = cur.fetchone()
            if r and r[0] == self.n:
                cur.execute("UPDATE pv_meta SET used=now() WHERE idx_key=%s", (self.key,)); return self
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")
            cur.execute(f"CREATE TABLE {tbl}(pos INT PRIMARY KEY, embedding public.vector({self.dim}) NOT NULL)")
            with cur.copy(f"COPY {tbl}(pos, embedding) FROM STDIN") as cp:
                for i, v in enumerate(X): cp.write_row((i, _lit(v)))
            cur.execute("SET maintenance_work_mem = '512MB'")
            cur.execute(f"CREATE INDEX {tbl}_hnsw ON {tbl} USING hnsw (embedding public.vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
            cur.execute("INSERT INTO pv_meta(idx_key, dim, n) VALUES(%s,%s,%s) ON CONFLICT (idx_key) DO UPDATE SET n=EXCLUDED.n, built=now(), used=now()", (self.key, self.dim, self.n))
            # 只保留最近使用的 8 组，其余删除
            cur.execute("SELECT idx_key FROM pv_meta ORDER BY used DESC OFFSET 8")
            for (k,) in cur.fetchall(): cur.execute(f"DROP TABLE IF EXISTS pv_{k}"); cur.execute("DELETE FROM pv_meta WHERE idx_key=%s", (k,))
        return self
    def size(self): return self.n
    def search(self, q, k=10):
        if not self.n: return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        k = min(k, self.n); lit = _lit(q); tbl = self.tbl
        with _conn().cursor() as cur:
            cur.execute(f"SET hnsw.ef_search = {max(k, self.ef_search)}")
            cur.execute(f"SELECT pos, embedding OPERATOR(public.<=>) %s::public.vector AS d FROM {tbl} ORDER BY d LIMIT %s", (lit, k))
            rows = cur.fetchall()
        rows.sort(key=lambda r: r[1])
        return np.array([r[0] for r in rows], dtype=np.int64), np.array([1.0 - float(r[1]) for r in rows], dtype=np.float32)
def status():
    st = available()
    return {"enabled_flag": ENABLED, **st, "backend": "pgvector" if st.get("ok") else None,
            "engine_setting": os.environ.get("PRICEHUB_VECTOR_ENGINE", "auto"), "min_items": int(os.environ.get("PRICEHUB_HNSW_MIN", "50000"))}
