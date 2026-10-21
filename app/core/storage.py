"""存储抽象层：同一套 q/ex/ex_many 接口，后端可选 SQLite（单机默认）或 PostgreSQL（PRICEHUB_DB_URL=postgresql://...）。
业务代码写 SQLite 方言的 SQL，PostgreSQL 后端在执行前做方言翻译（占位符、UPSERT、datetime('now')、LIKE 大小写、自增主键、RETURNING id）。"""
import sqlite3, os, json, threading, re, hashlib, decimal
from . import tenancy
DB_PATH = os.environ.get("PRICEHUB_DB", os.path.join(os.path.dirname(__import__('app').__file__), "..", "data", "pricehub.db"))
_lock = threading.Lock()
DB_URL = os.environ.get("PRICEHUB_DB_URL", "")
BACKEND = "postgresql" if DB_URL.startswith(("postgres://", "postgresql://")) else "sqlite"
VER = [0]   # 写操作版本号，用于接口缓存/向量索引失效
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, name TEXT, type TEXT, config TEXT, enabled INTEGER DEFAULT 1, workspace_id INTEGER NOT NULL DEFAULT 1, UNIQUE(workspace_id, name));
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, source_id INTEGER, platform TEXT, external_id TEXT, title TEXT,
  category TEXT, chain TEXT, brand TEXT, url TEXT, image_path TEXT, phash TEXT, clip TEXT, currency TEXT DEFAULT 'CNY', cost REAL,
  group_key TEXT, monitored INTEGER DEFAULT 1, workspace_id INTEGER NOT NULL DEFAULT 1, UNIQUE(workspace_id, platform, external_id));
CREATE TABLE IF NOT EXISTS prices(id INTEGER PRIMARY KEY, product_id INTEGER, price REAL, ts TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS platforms(id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT, country TEXT, region TEXT, type TEXT, scope TEXT, domain TEXT, url TEXT,
  lang TEXT, currency TEXT, note TEXT, strategy TEXT, search_url TEXT, rules TEXT, status TEXT, status_detail TEXT, http_code INTEGER, latency_ms INTEGER, checked_at TEXT,
  enabled INTEGER DEFAULT 0, source_id INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT, v TEXT, workspace_id INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(workspace_id, k));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY, name TEXT, type TEXT, url TEXT, config TEXT DEFAULT '{}', enabled INTEGER DEFAULT 1, workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY, name TEXT, type TEXT, value REAL, product_id INTEGER, group_key TEXT, category TEXT, channel_ids TEXT DEFAULT '[]',
  cooldown_min INTEGER DEFAULT 60, enabled INTEGER DEFAULT 1, last_fired TEXT, fired_count INTEGER DEFAULT 0, workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, channel_id INTEGER, rule_id INTEGER, event TEXT, ok INTEGER, detail TEXT, payload TEXT, ts TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS map_prices(group_key TEXT, price REAL, note TEXT, workspace_id INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(workspace_id, group_key));
CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, product_id INTEGER, price REAL, map_price REAL, url TEXT, path TEXT, sha256 TEXT, ts TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pricing_rules(id INTEGER PRIMARY KEY, name TEXT, strategy TEXT, param REAL DEFAULT 0, min_margin REAL DEFAULT 0.1, floor REAL, ceiling REAL,
  respect_map INTEGER DEFAULT 1, round_to TEXT DEFAULT '', category TEXT, enabled INTEGER DEFAULT 1, workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS tokens(id INTEGER PRIMARY KEY, name TEXT, token_hash TEXT UNIQUE, role TEXT, created TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')), actor TEXT, method TEXT, path TEXT, status INTEGER, ip TEXT, workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS dead_letters(id INTEGER PRIMARY KEY, kind TEXT, job_key TEXT, payload TEXT, error TEXT, attempts INTEGER, ts TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, product_id INTEGER, msg TEXT, ts TEXT DEFAULT (datetime('now')), workspace_id INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY, name TEXT, slug TEXT UNIQUE, plan TEXT DEFAULT 'free', quota TEXT DEFAULT '{}', created TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, password_hash TEXT, oidc_issuer TEXT, oidc_sub TEXT, created TEXT DEFAULT (datetime('now')), last_login TEXT);
CREATE TABLE IF NOT EXISTS memberships(id INTEGER PRIMARY KEY, user_id INTEGER, ws_id INTEGER, role TEXT, created TEXT DEFAULT (datetime('now')), UNIQUE(user_id, ws_id));
CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY, ws_id INTEGER, email TEXT, role TEXT, token_hash TEXT UNIQUE, created_by INTEGER, created TEXT DEFAULT (datetime('now')), accepted_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE, user_id INTEGER, ws_id INTEGER, created TEXT DEFAULT (datetime('now')), expires TEXT);
CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY, ws_id INTEGER, metric TEXT, period TEXT, n INTEGER DEFAULT 0, UNIQUE(ws_id, metric, period));
"""
MIGRATIONS = {"prices": [("confidence", "REAL"), ("conf_level", "TEXT"), ("conf_basis", "TEXT"), ("method", "TEXT"), ("in_stock", "INTEGER")],
              "products": [("seller", "TEXT"), ("attrs", "TEXT"), ("gtin", "TEXT"), ("model", "TEXT"), ("in_stock", "INTEGER"), ("shipping", "REAL"), ("tax_rate", "REAL"),
                           ("coupon", "REAL"), ("map_price", "REAL"), ("target_price", "REAL"), ("variant", "TEXT"), ("parent_id", "INTEGER"), ("rendered", "INTEGER")]}
INDEXES = ["CREATE INDEX IF NOT EXISTS ix_products_ws_id ON products(workspace_id, id)", "DROP INDEX IF EXISTS ix_products_ws", "CREATE INDEX IF NOT EXISTS ix_sources_ws ON sources(workspace_id)",
           "INSERT OR IGNORE INTO workspaces(id,name,slug,plan) VALUES(1,'默认工作区','default','desktop')","CREATE INDEX IF NOT EXISTS ix_prices_pid ON prices(product_id, id)", "CREATE INDEX IF NOT EXISTS ix_products_gk ON products(group_key)",
           "CREATE INDEX IF NOT EXISTS ix_products_platform ON products(platform)", "CREATE INDEX IF NOT EXISTS ix_alerts_pid ON alerts(product_id)"]
# ---------------- SQLite ----------------
def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30); c.row_factory = sqlite3.Row; return c
def _sqlite_init():
    with _lock, conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        exist = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old = []
        for t in REBUILD & exist:   # v7 多租户迁移：唯一约束加上 workspace_id，需要重建表
            if "workspace_id" not in {r[1] for r in c.execute(f"PRAGMA table_info({t})")}: c.execute(f"ALTER TABLE {t} RENAME TO {t}__v6"); old.append(t)
        for t in (tenancy.TENANT_TABLES - REBUILD) & exist:
            if "workspace_id" not in {r[1] for r in c.execute(f"PRAGMA table_info({t})")}: c.execute(f"ALTER TABLE {t} ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1")
        c.executescript(SCHEMA)
        for t in old:
            have = [r[1] for r in c.execute(f"PRAGMA table_info({t}__v6)")]; new_cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col, typ in MIGRATIONS.get(t, []):
                if col not in new_cols: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}"); new_cols.add(col)
            cols = ",".join(x for x in have if x in new_cols); c.execute(f"INSERT INTO {t}({cols}) SELECT {cols} FROM {t}__v6"); c.execute(f"DROP TABLE {t}__v6")
        for t, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col, typ in cols:
                if col not in have: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
        for ix in INDEXES: c.execute(ix)
        # 让查询规划器掌握各列的区分度（尤其 workspace_id 区分度低），避免误用工作区索引导致全表扫描
        c.execute("PRAGMA optimize=0x10002")
        c.commit()
def optimize():
    """大批量导入后调用：SQLite 更新统计信息；PostgreSQL 由 autovacuum 负责。"""
    if BACKEND == "sqlite":
        with _lock, conn() as c: c.execute("PRAGMA optimize=0x10002"); c.commit()
# ---------------- PostgreSQL ----------------
COPY_MIN = int(os.environ.get("PRICEHUB_COPY_MIN", "200"))
_COUNT = {}
def count(table, where="1=1", args=(), ttl=30):
    """带缓存的计数：同一条件 ttl 秒内复用；PostgreSQL 无条件计数且表很大时用统计信息估算（返回 (数量, 是否精确)）。"""
    import time as _t
    key = (table, where, tuple(args)); hit = _COUNT.get(key)
    if hit and _t.time() - hit[0] < ttl and hit[2] == VER[0] // 1000: return hit[1], hit[3]
    exact = True
    if BACKEND == "postgresql" and where == "1=1":
        est = (q("SELECT reltuples::bigint n FROM pg_class WHERE oid=to_regclass(?)", (table,), one=True) or {}).get("n") or 0
        if est > 200_000: n, exact = int(est), False
        else: n = q(f"SELECT COUNT(*) n FROM {table}", one=True)["n"]
    else: n = q(f"SELECT COUNT(*) n FROM {table} WHERE {where}", args, one=True)["n"]
    if len(_COUNT) > 500: _COUNT.clear()
    _COUNT[key] = (_t.time(), n, VER[0] // 1000, exact); return n, exact
UPSERT_KEY = {"settings": "workspace_id,k", "map_prices": "workspace_id,group_key", "sources": "workspace_id,name", "platforms": "slug", "usage": "ws_id,metric,period", "memberships": "user_id,ws_id"}
NO_ID = {"settings", "map_prices"}
REBUILD = {"sources", "products", "settings", "map_prices"}   # 唯一约束需要加上 workspace_id 的表，旧库迁移时重建
_PG = threading.local(); PG_SCHEMA = [None]
_PG_WS = "COALESCE(NULLIF(current_setting('app.ws', true), ''), '1')::bigint"
_PG_POLICY = f"current_setting('app.bypass', true) = '1' OR workspace_id = {_PG_WS}"
_NOW = "to_char(now() at time zone 'utc','YYYY-MM-DD HH24:MI:SS')"
def _pg_schema():
    if PG_SCHEMA[0] is None:
        s = os.environ.get("PRICEHUB_PG_SCHEMA")
        if not s and os.environ.get("PRICEHUB_PG_SCHEMA_FROM_DB") == "1": s = "t_" + hashlib.md5(os.path.abspath(DB_PATH).encode()).hexdigest()[:12]
        PG_SCHEMA[0] = s or "public"
    return PG_SCHEMA[0]
def pg_conn():
    c = getattr(_PG, "c", None)
    if c is None or c.closed:
        import psycopg
        c = psycopg.connect(DB_URL, autocommit=False); sch = _pg_schema(); _PG.ws = None
        with c.cursor() as cur:
            if sch != "public": cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{sch}"')
            cur.execute(f'SET search_path TO "{sch}"')
        c.commit(); _PG.c = c
    return c
def _split_literals(sql):
    """把 SQL 切成 (是否字符串字面量, 片段) 列表，翻译时只改字面量之外的部分。"""
    out, i, buf = [], 0, ""
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if buf: out.append((False, buf)); buf = ""
            j = i + 1
            while j < len(sql):
                if sql[j] == "'" and j + 1 < len(sql) and sql[j + 1] == "'": j += 2; continue
                if sql[j] == "'": break
                j += 1
            out.append((True, sql[i:j + 1])); i = j + 1; continue
        buf += ch; i += 1
    if buf: out.append((False, buf))
    return out
_CACHE_T = {}
def translate(sql):
    """SQLite 方言 → PostgreSQL。"""
    if sql in _CACHE_T: return _CACHE_T[sql]
    s = sql.strip(); up = s.upper(); returning = False
    s = re.sub(r"datetime\('now'\s*,\s*'(-?\d+) (day|days|hour|hours|minute|minutes)'\)", lambda m: f"to_char(now() at time zone 'utc' + interval '{m.group(1)} {m.group(2)}','YYYY-MM-DD HH24:MI:SS')", s, flags=re.I)
    s = re.sub(r"datetime\('now'\)", _NOW, s, flags=re.I)
    m = re.match(r"INSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+(\w+)\s*\(([^)]*)\)", s, re.I)
    tail = ""
    if m:
        kind, table, cols = m.group(1).upper(), m.group(2), [c.strip() for c in m.group(3).split(",")]
        s = "INSERT INTO" + s[m.end(0) - len(m.group(0)) + len(re.match(r"INSERT\s+OR\s+\w+\s+INTO", m.group(0), re.I).group(0)):]
        key = UPSERT_KEY.get(table, "id")
        if kind == "IGNORE": tail = " ON CONFLICT DO NOTHING"
        else:
            sets = [f"{c}=EXCLUDED.{c}" for c in cols if c != key]
            tail = f" ON CONFLICT ({key}) DO " + ("UPDATE SET " + ",".join(sets) if sets else "NOTHING")
        if table not in NO_ID: tail += " RETURNING id"; returning = True
    else:
        mi = re.match(r"INSERT\s+INTO\s+(\w+)", s, re.I)
        if mi and mi.group(1) not in NO_ID and "RETURNING" not in up: tail = " RETURNING id"; returning = True
    parts = []
    for lit, seg in _split_literals(s):
        if lit: parts.append(seg.replace("%", "%%")); continue
        seg = seg.replace("%", "%%").replace("?", "%s")
        seg = re.sub(r"\bLIKE\b", "ILIKE", seg, flags=re.I)
        seg = re.sub(r"\bGROUP_CONCAT\(", "STRING_AGG(", seg, flags=re.I)
        parts.append(seg)
    out = ("".join(parts) + tail, returning); _CACHE_T[sql] = out; return out
def _schema_pg():
    s = SCHEMA.replace("workspace_id INTEGER NOT NULL DEFAULT 1", "workspace_id BIGINT NOT NULL DEFAULT " + _PG_WS).replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION").replace("(datetime('now'))", "(" + _NOW + ")")
    return [x.strip() for x in s.split(";") if x.strip()]
def _fix(v):
    if isinstance(v, decimal.Decimal): return float(v)
    return v
def _pg_init():
    with _lock:
        c = pg_conn()
        with c.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (_pg_schema(),)); exist = {r[0] for r in cur.fetchall()}
            for t in tenancy.TENANT_TABLES & exist:   # v7 多租户迁移：补 workspace_id，并把唯一约束改为含 workspace_id
                cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name='workspace_id'", (_pg_schema(), t))
                if cur.fetchone(): continue
                cur.execute(f"ALTER TABLE {t} ADD COLUMN workspace_id BIGINT NOT NULL DEFAULT {_PG_WS}")
                if t in REBUILD:
                    cur.execute("SELECT conname, contype FROM pg_constraint WHERE conrelid=%s::regclass AND contype IN ('u','p')", (t,))
                    for name, typ in cur.fetchall():
                        if not (typ == "p" and t in ("sources", "products")): cur.execute(f'ALTER TABLE {t} DROP CONSTRAINT "{name}"')
                    key = {"sources": "UNIQUE(workspace_id, name)", "products": "UNIQUE(workspace_id, platform, external_id)", "settings": "PRIMARY KEY(workspace_id, k)", "map_prices": "PRIMARY KEY(workspace_id, group_key)"}[t]
                    cur.execute(f"ALTER TABLE {t} ADD {key}")
            for st in _schema_pg(): cur.execute(st)
            for t in tenancy.TENANT_TABLES:   # 行级安全：每条语句只能看到/写入当前工作区的数据
                cur.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"); cur.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
                cur.execute(f"DROP POLICY IF EXISTS ws_isolation ON {t}"); cur.execute(f"CREATE POLICY ws_isolation ON {t} USING ({_PG_POLICY}) WITH CHECK ({_PG_POLICY})")
            for t, cols in MIGRATIONS.items():
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s", (_pg_schema(), t)); have = {r[0] for r in cur.fetchall()}
                for col, typ in cols:
                    if col not in have: cur.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ.replace('REAL', 'DOUBLE PRECISION')}")
            for ix in INDEXES: cur.execute(translate(ix)[0] if ix.startswith("INSERT") else ix)
            cur.execute("SELECT setval(pg_get_serial_sequence('workspaces','id'), GREATEST((SELECT MAX(id) FROM workspaces), 1))")
        c.commit()
def _pg_ws(cur):
    want = ("1" if tenancy.bypassed() else "0", str(tenancy.current()))
    if getattr(_PG, "ws", None) != want:
        cur.execute("SELECT set_config('app.bypass', %s, false), set_config('app.ws', %s, false)", want); _PG.ws = want
def _pg_exec(sql, args, fetch):
    tsql, returning = translate(sql)
    with _lock:
        c = pg_conn()
        try:
            with c.cursor() as cur:
                _pg_ws(cur)
                cur.execute(tsql, tuple(args) if args else None)
                rows = None; rid = None
                if cur.description is not None:
                    names = [d.name for d in cur.description]; data = cur.fetchall()
                    if returning and not fetch: rid = data[0][0] if data else None
                    else: rows = [{k: _fix(v) for k, v in zip(names, r)} for r in data]
                rc = cur.rowcount
            c.commit(); return rows, rid, rc
        except Exception:
            c.rollback(); raise
# ---------------- 统一接口 ----------------
def init(): return _pg_init() if BACKEND == "postgresql" else _sqlite_init()
def q(sql, args=(), one=False):
    if BACKEND == "postgresql":
        rows, _, rc = _pg_exec(sql, args, True); rows = rows or []
        if not sql.lstrip().upper().startswith("SELECT"): VER[0] += 1
        return (rows[0] if rows else None) if one else rows
    if not tenancy.bypassed(): sql = tenancy.rewrite(sql, tenancy.current())
    with _lock, conn() as c:
        cur = c.execute(sql, args); rows = [dict(r) for r in cur.fetchall()]
        if c.total_changes: VER[0] += 1
        c.commit(); return (rows[0] if rows else None) if one else rows
def ex(sql, args=()):
    if BACKEND == "postgresql":
        _, rid, rc = _pg_exec(sql, args, False); VER[0] += 1
        if rid is None and re.match(r"\s*INSERT", sql, re.I): return 0 if not rc or rc < 0 else rc
        return rid if rid is not None else rc
    if not tenancy.bypassed(): sql = tenancy.rewrite(sql, tenancy.current())
    with _lock, conn() as c:
        cur = c.execute(sql, args); c.commit(); VER[0] += 1; return cur.lastrowid
def ex_many(sql, rows):
    if BACKEND == "postgresql":
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\(\s*\?(\s*,\s*\?)*\s*\)\s*$", sql, re.I)
        if m and len(rows) >= COPY_MIN:   # 纯批量插入走 COPY 协议，比逐条 INSERT 快数倍
            cols = ",".join(c.strip() for c in m.group(2).split(","))
            with _lock:
                c = pg_conn()
                try:
                    with c.cursor() as cur0: _pg_ws(cur0)
                    # 业务表启用了行级安全，PostgreSQL 不允许直接 COPY 进去：先 COPY 到无 RLS 的临时表，再一条 INSERT…SELECT 搬入（仍受 RLS 校验）
                    t = m.group(1); stg = f"_stg_{t}"
                    with c.cursor() as cur:
                        cur.execute(f"CREATE TEMP TABLE {stg} ON COMMIT DROP AS SELECT {cols} FROM {t} WITH NO DATA")
                        with cur.copy(f"COPY {stg} ({cols}) FROM STDIN") as cp:
                            for r in rows: cp.write_row(r)
                        cur.execute(f"INSERT INTO {t} ({cols}) SELECT {cols} FROM {stg}")
                    c.commit()
                except Exception: c.rollback(); raise
            VER[0] += 1; return
        tsql, returning = translate(sql)
        if returning: tsql = tsql[: -len(" RETURNING id")]
        with _lock:
            c = pg_conn()
            try:
                with c.cursor() as cur: _pg_ws(cur); cur.executemany(tsql, rows)
                c.commit()
            except Exception: c.rollback(); raise
        VER[0] += 1; return
    if not tenancy.bypassed(): sql = tenancy.rewrite(sql, tenancy.current())
    with _lock, conn() as c: c.executemany(sql, rows); c.commit(); VER[0] += 1
def info(): return {"backend": BACKEND, "path": None if BACKEND == "postgresql" else os.path.abspath(DB_PATH), "schema": _pg_schema() if BACKEND == "postgresql" else None}
# ---------------- 跨后端备份 / 迁移 ----------------
def tables(): return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
def export_sqlite(out):
    """把当前库（任意后端）导出为 SQLite 文件：既是备份格式，也是 SQLite ↔ PostgreSQL 迁移介质。"""
    if BACKEND == "sqlite":
        with sqlite3.connect(DB_PATH) as src, sqlite3.connect(out) as dst: src.backup(dst)
        return out
    dst = sqlite3.connect(out); dst.executescript(SCHEMA)
    for t, cols in MIGRATIONS.items():
        have = {r[1] for r in dst.execute(f"PRAGMA table_info({t})")}
        for col, typ in cols:
            if col not in have: dst.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
    for t in tables():
        rows = q(f"SELECT * FROM {t}")
        if rows:
            cols = list(rows[0]); dst.executemany(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [tuple(r[c] for c in cols) for r in rows])
    dst.commit(); dst.close(); return out
def import_sqlite(path):
    """用 SQLite 备份文件整体替换当前库内容（PostgreSQL 后端）。"""
    src = sqlite3.connect(path); src.row_factory = sqlite3.Row
    with _lock:
        c = pg_conn()
        try:
            with c.cursor() as cur:
                for t in tables():
                    try: rows = src.execute(f"SELECT * FROM {t}").fetchall()
                    except sqlite3.OperationalError: continue
                    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s", (_pg_schema(), t)); have = {r[0] for r in cur.fetchall()}
                    cur.execute(f"DELETE FROM {t}")
                    if rows:
                        cols = [k for k in rows[0].keys() if k in have]
                        cur.executemany(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))})", [tuple(r[k] for k in cols) for r in rows])
                    if t not in NO_ID: cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")
            c.commit()
        except Exception: c.rollback(); raise
        finally: src.close()
