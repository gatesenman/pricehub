import sqlite3, os, json, threading
DB_PATH = os.environ.get("PRICEHUB_DB", os.path.join(os.path.dirname(__file__), "..", "data", "pricehub.db"))
_lock = threading.Lock()
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
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, product_id INTEGER, msg TEXT, ts TEXT DEFAULT (datetime('now')));
"""
def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False); c.row_factory = sqlite3.Row; return c
MIGRATIONS = {"prices": [("confidence", "REAL"), ("conf_level", "TEXT"), ("conf_basis", "TEXT"), ("method", "TEXT"), ("in_stock", "INTEGER")],
              "products": [("seller", "TEXT"), ("attrs", "TEXT"), ("gtin", "TEXT"), ("model", "TEXT"), ("in_stock", "INTEGER"), ("shipping", "REAL"), ("tax_rate", "REAL"),
                           ("coupon", "REAL"), ("map_price", "REAL"), ("target_price", "REAL"), ("variant", "TEXT"), ("parent_id", "INTEGER"), ("rendered", "INTEGER")]}
def init():
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        for t, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col, typ in cols:
                if col not in have: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
        c.execute("CREATE INDEX IF NOT EXISTS ix_prices_pid ON prices(product_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_products_gk ON products(group_key)")
        c.commit()
def q(sql, args=(), one=False):
    with _lock, conn() as c:
        cur = c.execute(sql, args); rows = [dict(r) for r in cur.fetchall()]
        if c.total_changes: VER[0] += 1
        c.commit(); return (rows[0] if rows else None) if one else rows
def ex(sql, args=()):
    with _lock, conn() as c:
        cur = c.execute(sql, args); c.commit(); VER[0] += 1; return cur.lastrowid

def ex_many(sql, rows):
    with _lock, conn() as c: c.executemany(sql, rows); c.commit(); VER[0] += 1
