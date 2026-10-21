"""可选的 PostgreSQL pgvector 向量后端。
有 PG 且安装了 vector 扩展时可用；否则 list/search 会说明原因并跳过，不影响 FAISS/numpy。
环境变量：PRICEHUB_PGVECTOR=1 启用尝试。
"""
from __future__ import annotations
import os, json, logging
log = logging.getLogger("pricehub.pgvector")
ENABLED = os.environ.get("PRICEHUB_PGVECTOR", "0") == "1"
_STATE = {"ok": None, "error": None, "dim": None}
def available():
    if not ENABLED: return {"ok": False, "reason": "未开启（设 PRICEHUB_PGVECTOR=1）"}
    if _STATE["ok"] is False: return {"ok": False, "reason": _STATE["error"]}
    if _STATE["ok"]: return {"ok": True, "dim": _STATE["dim"]}
    try:
        from app.core import storage as db
        if db.BACKEND != "postgresql":
            _STATE.update(ok=False, error="当前不是 PostgreSQL 后端"); return available()
        with db.pg_conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
            if not cur.fetchone():
                try: cur.execute("CREATE EXTENSION IF NOT EXISTS vector"); c.commit()
                except Exception as e:
                    _STATE.update(ok=False, error=f"无法创建 vector 扩展：{e}（请 DBA 安装 pgvector）"); return available()
        _STATE.update(ok=True, error=None); return {"ok": True, "dim": _STATE["dim"]}
    except Exception as e:
        _STATE.update(ok=False, error=str(e)); return available()
def ensure_table(dim: int):
    st = available()
    if not st["ok"]: return st
    from app.core import storage as db
    with db.pg_conn() as c, c.cursor() as cur:
        cur.execute(f"""CREATE TABLE IF NOT EXISTS product_embeddings(
            product_id BIGINT PRIMARY KEY, workspace_id INT NOT NULL DEFAULT 1,
            embedding vector({int(dim)}), updated TIMESTAMPTZ DEFAULT now())""")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_pe_ws ON product_embeddings(workspace_id)")
        # ivfflat / hnsw 需要数据量；先跳过索引创建失败
        try: cur.execute(f"CREATE INDEX IF NOT EXISTS ix_pe_hnsw ON product_embeddings USING hnsw (embedding vector_cosine_ops)")
        except Exception as e: log.info("hnsw index skipped", extra={"error": str(e)})
        c.commit()
    _STATE["dim"] = dim; return {"ok": True, "dim": dim}
def upsert(product_id: int, vec, workspace_id: int = 1):
    st = available()
    if not st["ok"]: return st
    import numpy as np
    v = np.asarray(vec, dtype="float32").tolist()
    if _STATE["dim"] is None: ensure_table(len(v))
    from app.core import storage as db
    lit = "[" + ",".join(f"{x:.8f}" for x in v) + "]"
    with db.pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO product_embeddings(product_id, workspace_id, embedding) VALUES(%s,%s,%s::vector) ON CONFLICT (product_id) DO UPDATE SET embedding=EXCLUDED.embedding, workspace_id=EXCLUDED.workspace_id, updated=now()",
                    (product_id, workspace_id, lit)); c.commit()
    return {"ok": True}
def search(vec, k=10, workspace_id=None):
    st = available()
    if not st["ok"]: return [], st
    import numpy as np
    v = np.asarray(vec, dtype="float32").tolist()
    lit = "[" + ",".join(f"{x:.8f}" for x in v) + "]"
    from app.core import storage as db
    sql = "SELECT product_id, 1 - (embedding <=> %s::vector) AS score FROM product_embeddings"
    args = [lit]
    if workspace_id is not None: sql += " WHERE workspace_id=%s"; args.append(workspace_id)
    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"; args += [lit, k]
    with db.pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args); rows = cur.fetchall()
    return [{"product_id": r[0], "score": float(r[1])} for r in rows], {"ok": True, "engine": "pgvector"}
def status():
    st = available()
    return {"enabled_flag": ENABLED, **st, "backend": "pgvector" if st.get("ok") else None}
