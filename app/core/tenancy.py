"""多租户：当前工作区上下文 + SQLite 的 SQL 改写隔离（PostgreSQL 用行级安全 RLS，见 storage）。

- 当前工作区保存在 contextvar 中：请求中间件按会话/令牌设置，后台任务从任务载荷恢复，默认 1（单机默认工作区）。
- SQLite：对涉及租户表的语句用 sqlglot 改写：SELECT/UPDATE/DELETE 追加 workspace_id 条件（JOIN 加在 ON 上，保持 LEFT JOIN 语义），
  INSERT 自动补 workspace_id 列。改写结果按 (SQL, 工作区) 缓存。
"""
import contextvars, contextlib, re, threading
import sqlglot
from sqlglot import exp
TENANT_TABLES = {"sources", "products", "prices", "channels", "rules", "notifications", "map_prices", "evidence", "pricing_rules", "tokens", "audit", "alerts", "dead_letters", "settings", "dedup_candidates", "product_merges"}
_WS = contextvars.ContextVar("pricehub_ws", default=1)
_BYPASS = contextvars.ContextVar("pricehub_ws_bypass", default=False)
def current(): return _WS.get()
def set_current(ws): return _WS.set(int(ws))
def bypassed(): return _BYPASS.get()
@contextlib.contextmanager
def use(ws):
    tok = _WS.set(int(ws))
    try: yield
    finally: _WS.reset(tok)
@contextlib.contextmanager
def bypass():
    """系统级操作（跨工作区统计、迁移）临时关闭隔离。"""
    tok = _BYPASS.set(True)
    try: yield
    finally: _BYPASS.reset(tok)
def wrap(fn):
    """把当前上下文（含工作区）带进其他线程执行。"""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.run(fn, *a, **k)
_RX = re.compile(r"\b(" + "|".join(sorted(TENANT_TABLES)) + r")\b", re.I)
_CACHE = {}; _LOCK = threading.Lock()
def _cond(name, ws): return exp.EQ(this=exp.column("workspace_id", table=name), expression=exp.Literal.number(ws))
def _rewrite_select(sel, ws):
    frm = sel.args.get("from_") or sel.args.get("from")
    if frm is not None:
        for t in [frm.this] if not isinstance(frm.this, list) else frm.this:
            if isinstance(t, exp.Table) and t.name.lower() in TENANT_TABLES:
                sel.where(_cond(t.alias_or_name, ws), copy=False)
    for j in sel.args.get("joins") or []:
        t = j.this
        if isinstance(t, exp.Table) and t.name.lower() in TENANT_TABLES:
            on = j.args.get("on"); c = _cond(t.alias_or_name, ws)
            j.set("on", exp.and_(on, c) if on is not None else c)
def rewrite(sql, ws):
    if not _RX.search(sql): return sql
    key = (sql, ws)
    if key in _CACHE: return _CACHE[key]
    head = sql.lstrip()[:12].upper()
    if head.startswith(("PRAGMA", "CREATE", "ALTER", "DROP", "BEGIN", "COMMIT", "ANALYZE", "VACUUM")): return sql
    tree = sqlglot.parse_one(sql, read="sqlite")
    if isinstance(tree, exp.Insert):
        tbl = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        if isinstance(tbl, exp.Table) and tbl.name.lower() in TENANT_TABLES and isinstance(tree.this, exp.Schema):
            cols = [c.name.lower() for c in tree.this.expressions]
            if "workspace_id" not in cols:
                tree.this.append("expressions", exp.to_identifier("workspace_id"))
                src = tree.expression
                if isinstance(src, exp.Values):
                    for tup in src.expressions: tup.append("expressions", exp.Literal.number(ws))
                elif isinstance(src, exp.Select): src.select(exp.Literal.number(ws), copy=False)
        for s in tree.find_all(exp.Select): _rewrite_select(s, ws)
    elif isinstance(tree, (exp.Update, exp.Delete)):
        t = tree.this
        if isinstance(t, exp.Table) and t.name.lower() in TENANT_TABLES:
            tree.where(exp.EQ(this=exp.column("workspace_id"), expression=exp.Literal.number(ws)), copy=False)
        for s in tree.find_all(exp.Select): _rewrite_select(s, ws)
    else:
        for s in list(tree.find_all(exp.Select)): _rewrite_select(s, ws)
    out = tree.sql(dialect="sqlite")
    with _LOCK:
        if len(_CACHE) > 20000: _CACHE.clear()
        _CACHE[key] = out
    return out
