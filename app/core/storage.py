"""存储抽象层：同一套 q/ex/ex_many 接口，后端可选 SQLite（单机默认）或 PostgreSQL（PRICEHUB_DB_URL=postgresql://...）。
业务代码写 SQLite 方言的 SQL，PostgreSQL 后端在执行前做方言翻译（占位符、UPSERT、datetime('now')、LIKE 大小写、自增主键、RETURNING id）。"""
import sqlite3, os, json, threading, re, hashlib, decimal
DB_PATH = os.environ.get("PRICEHUB_DB", os.path.join(os.path.dirname(__import__('app').__file__), "..", "data", "pricehub.db"))
_lock = threading.Lock()
DB_URL = os.environ.get("PRICEHUB_DB_URL", "")
BACKEND = "postgresql" if DB_URL.startswith(("postgres://", "postgresql://")) else "sqlite"
VER = [0]   # 写操作版本号，用于接口缓存/向量索引失效
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, config TEXT, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, source_id INTEGER, platform TEXT, external_id TEXT, title TEXT,
  category TEXT, chain TEXT, brand TEXT, url TEXT, image_path TEXT, phash TEXT, clip TEXT, currency TEXT DEFAULT 'CNY', cost REAL,
  group_key TEXT, monitored INTEGER DEFAULT 1, UNIQUE(platform, external_id));
CREATE TABLE IF NOT EXISTS prices(id INTEGER PRIMARY KEY, product_id INTEGER, price REAL, ts TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS platforms(id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT, country TEXT, region TEXT, type TEXT, scope TEXT, domain TEXT, url TEXT,
  lang TEXT, currency TEXT, note TEXT, strategy TEXT, search_url TEXT, rules TEXT, status TEXT, status_detail TEXT, http_code INTEGER, latency_ms INTEGER, checked_at TEXT,
  enabled INTEGER DEFAULT 0, source_id INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY, name TEXT, type TEXT, url TEXT, config TEXT DEFAULT '{}', enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY, name TEXT, type TEXT, value REAL, product_id INTEGER, group_key TEXT, category TEXT, channel_ids TEXT DEFAULT '[]',
  cooldown_min INTEGER DEFAULT 60, enabled INTEGER DEFAULT 1, last_fired TEXT, fired_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, channel_id INTEGER, rule_id INTEGER, event TEXT, ok INTEGER, detail TEXT, payload TEXT, ts TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS map_prices(group_key TEXT PRIMARY KEY, price REAL, note TEXT);
CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, product_id INTEGER, price REAL, map_price REAL, url TEXT, path TEXT, sha256 TEXT, ts TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS pricing_rules(id INTEGER PRIMARY KEY, name TEXT, strategy TEXT, param REAL DEFAULT 0, min_margin REAL DEFAULT 0.1, floor REAL, ceiling REAL,
  respect_map INTEGER DEFAULT 1, round_to TEXT DEFAULT '', category TEXT, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS tokens(id INTEGER PRIMARY KEY, name TEXT, token_hash TEXT UNIQUE, role TEXT, created TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')), actor TEXT, method TEXT, path TEXT, status INTEGER, ip TEXT);
CREATE TABLE IF NOT EXISTS dead_letters(id INTEGER PRIMARY KEY, kind TEXT, job_key TEXT, payload TEXT, error TEXT, attempts INTEGER, ts TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, product_id INTEGER, msg TEXT, ts TEXT DEFAULT (datetime('now')));
"""
MIGRATIONS = {"prices": [("confidence", "REAL"), ("conf_level", "TEXT"), ("conf_basis", "TEXT"), ("method", "TEXT"), ("in_stock", "INTEGER")],
              "products": [("seller", "TEXT"), ("attrs", "TEXT"), ("gtin", "TEXT"), ("model", "TEXT"), ("in_stock", "INTEGER"), ("shipping", "REAL"), ("tax_rate", "REAL"),
                           ("coupon", "REAL"), ("map_price", "REAL"), ("target_price", "REAL"), ("variant", "TEXT"), ("parent_id", "INTEGER"), ("rendered", "INTEGER")]}
INDEXES = ["CREATE INDEX IF NOT EXISTS ix_prices_pid ON prices(product_id, id)", "CREATE INDEX IF NOT EXISTS ix_products_gk ON products(group_key)",
           "CREATE INDEX IF NOT EXISTS ix_products_platform ON products(platform)", "CREATE INDEX IF NOT EXISTS ix_alerts_pid ON alerts(product_id)"]
# ---------------- SQLite ----------------
def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30); c.row_factory = sqlite3.Row; return c
def _sqlite_init():
    with _lock, conn() as c:
        c.execute("PRAGMA journal_mode=WAL"); c.executescript(SCHEMA)
        for t, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col, typ in cols:
                if col not in have: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
        for ix in INDEXES: c.execute(ix)
        c.commit()
# ---------------- PostgreSQL ----------------
UPSERT_KEY = {"settings": "k", "map_prices": "group_key", "sources": "name", "platforms": "slug"}
NO_ID = {"settings", "map_prices"}
_PG = threading.local(); PG_SCHEMA = [None]
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
        c = psycopg.connect(DB_URL, autocommit=False); sch = _pg_schema()
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
    s = SCHEMA.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION").replace("(datetime('now'))", "(" + _NOW + ")")
    return [x.strip() for x in s.split(";") if x.strip()]
def _fix(v):
    if isinstance(v, decimal.Decimal): return float(v)
    return v
def _pg_init():
    with _lock:
        c = pg_conn()
        with c.cursor() as cur:
            for st in _schema_pg(): cur.execute(st)
            for t, cols in MIGRATIONS.items():
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s", (_pg_schema(), t)); have = {r[0] for r in cur.fetchall()}
                for col, typ in cols:
                    if col not in have: cur.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ.replace('REAL', 'DOUBLE PRECISION')}")
            for ix in INDEXES: cur.execute(ix)
        c.commit()
def _pg_exec(sql, args, fetch):
    tsql, returning = translate(sql)
    with _lock:
        c = pg_conn()
        try:
            with c.cursor() as cur:
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
    with _lock, conn() as c:
        cur = c.execute(sql, args); rows = [dict(r) for r in cur.fetchall()]
        if c.total_changes: VER[0] += 1
        c.commit(); return (rows[0] if rows else None) if one else rows
def ex(sql, args=()):
    if BACKEND == "postgresql":
        _, rid, rc = _pg_exec(sql, args, False); VER[0] += 1
        if rid is None and re.match(r"\s*INSERT", sql, re.I): return None if rc == 0 else (rc or None)
        return rid if rid is not None else rc
    with _lock, conn() as c:
        cur = c.execute(sql, args); c.commit(); VER[0] += 1; return cur.lastrowid
def ex_many(sql, rows):
    if BACKEND == "postgresql":
        tsql, returning = translate(sql)
        if returning: tsql = tsql[: -len(" RETURNING id")]
        with _lock:
            c = pg_conn()
            try:
                with c.cursor() as cur: cur.executemany(tsql, rows)
                c.commit()
            except Exception: c.rollback(); raise
        VER[0] += 1; return
    with _lock, conn() as c: c.executemany(sql, rows); c.commit(); VER[0] += 1
def info(): return {"backend": BACKEND, "path": None if BACKEND == "postgresql" else os.path.abspath(DB_PATH), "schema": _pg_schema() if BACKEND == "postgresql" else None}
