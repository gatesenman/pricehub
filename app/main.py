import os, json, csv, tempfile, httpx, hashlib
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from . import db, sources, analytics, search, ai_search, confidence as CF, platform_registry, healthcheck
import threading, asyncio
HERE = os.path.dirname(__file__); IMG_DIR = os.path.join(HERE, "..", "data", "images"); os.makedirs(IMG_DIR, exist_ok=True)
app = FastAPI(title="PriceHub 全球交易平台价格全生命周期管理")
from .core.perf_middleware import install as _install_perf; _install_perf(app)  # v14：gzip + 静态缓存
db.init()
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
def load_platforms():
    p = os.path.join(HERE, "..", "data", "platforms.csv")
    if os.path.exists(p):
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        return [dict(region=r["大洲/地区"], country=r["国家"], name=r["平台名称"], url=r.get("网址") or "", type=r.get("类型") or "",
                     scope=r.get("本土或国际") or "", note=r.get("简要说明") or "") for r in rows if r.get("网址")]
    return json.load(open(os.path.join(HERE, "platforms.json")))
def seed_platforms():
    seen = set(); n = 0
    for r in platform_registry.load_rows():
        while r["slug"] in seen: r["slug"] += "x"
        seen.add(r["slug"]); cols = [k for k in r]
        n += db.ex(f"INSERT OR IGNORE INTO platforms({','.join(cols)}) VALUES({','.join('?'*len(cols))})", [r[k] for k in cols]) and 1
    return n
seed_platforms()
PLATFORMS = db.q("SELECT name,country,region,type,scope,url,note FROM platforms")

def _save_image(url_or_path, pid):
    try:
        path = url_or_path
        if url_or_path.startswith("http"):
            path = os.path.join(IMG_DIR, f"{pid}.img"); open(path, "wb").write(httpx.get(url_or_path, timeout=15).content)
        f = search.image_features(path); db.ex("UPDATE products SET image_path=?,phash=?,clip=? WHERE id=?", (path, f["phash"], f.get("clip"), pid))
    except Exception as e: print("image fail", e)

def _conf_of(r):
    return {"confidence": r.get("confidence"), "level": r.get("conf_level"), "basis": json.loads(r.get("conf_basis") or "[]"), "method": r.get("method")}
def _group_latest(gk, exclude_pid):
    return [r["price"] for r in db.q("SELECT (SELECT price FROM prices WHERE product_id=p.id ORDER BY id DESC LIMIT 1) price FROM products p WHERE group_key=? AND id<>?", (gk, exclude_pid)) if r["price"]]
from .core.events import bus
HOOKS = []   # 兼容 v5 的回调列表；v6 起统一走事件总线，HOOKS 作为总线订阅者被调用
TOPIC = {"price": "price.recorded", "stock": "stock.changed"}
def _fire(event, **kw): bus.publish(TOPIC.get(event, event), **kw)
def _legacy_hooks(topic, **kw):
    ev = {v: k for k, v in TOPIC.items()}.get(topic)
    for h in HOOKS:
        try: h(ev, **kw)
        except Exception as e: __import__("logging").getLogger("pricehub").warning("hook fail", extra={"event": ev, "error": str(e)})
bus.subscribe("price.recorded", _legacy_hooks, "alerting.rules"); bus.subscribe("stock.changed", _legacy_hooks, "alerting.rules")
def record_price(pid, price, method, ts=None, in_stock=None):
    p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True); listing = None
    if p and p.get("merged_into"):   # v12：已合并的商品页继续采到的价格，记到主商品名下并保留来源商品页
        listing = pid; pid = p["merged_into"]; p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    hist = [r["price"] for r in db.q("SELECT price FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 10", (pid,))]
    c = CF.score_price(price, method, hist, _group_latest(p["group_key"], pid), p.get("currency"), p.get("title"))
    db.ex("INSERT INTO prices(product_id,price,ts,confidence,conf_level,conf_basis,method,in_stock,listing_id) VALUES(?,?,COALESCE(?,datetime('now')),?,?,?,?,?,?)",
          (pid, price, ts, c["confidence"], c["level"], json.dumps(c["basis"], ensure_ascii=False), method, in_stock, listing))
    if not ts: _fire("price", pid=pid, price=price, prev=hist[0] if hist else None)
    return c
def migrate_confidence():
    """为已有价格记录补算置信度（幂等，仅处理 confidence 为空的记录）。v6：单次扫描 + 内存中维护历史 + 批量写回，百万级价格点可在分钟内完成。"""
    if not db.q("SELECT id FROM prices WHERE confidence IS NULL LIMIT 1", one=True): return 0
    rows = db.q("SELECT pr.id, pr.product_id, pr.price, pr.ts, pr.method, pr.confidence, s.type stype, s.config sconf, p.currency, p.title FROM prices pr JOIN products p ON p.id=pr.product_id LEFT JOIN sources s ON s.id=p.source_id "
                "WHERE pr.product_id IN (SELECT DISTINCT product_id FROM prices WHERE confidence IS NULL) ORDER BY pr.product_id, pr.ts, pr.id")
    batch, n, cur, hist = [], 0, None, []
    for r in rows:
        if r["product_id"] != cur: cur, hist = r["product_id"], []
        if r["confidence"] is None:
            m = r["method"] or {"mock": "mock", "rest": "api", "csv": "structured", None: "manual"}.get(r["stype"]) or ("css" if '"price_selector"' in (r["sconf"] or "") else "regex")
            c = CF.score_price(r["price"], m, hist[-10:][::-1], [], r["currency"], r["title"]); c["basis"].append("（迁移补算）")
            batch.append((c["confidence"], c["level"], json.dumps(c["basis"], ensure_ascii=False), m, r["id"])); n += 1
            if len(batch) >= 5000: db.ex_many("UPDATE prices SET confidence=?,conf_level=?,conf_basis=?,method=? WHERE id=?", batch); batch = []
        hist.append(r["price"])
    if batch: db.ex_many("UPDATE prices SET confidence=?,conf_level=?,conf_basis=?,method=? WHERE id=?", batch)
    return n
MIGRATED = migrate_confidence()

def upsert(src_id, it):
    plat = it.get("platform") or "unknown"; ex = db.q("SELECT * FROM products WHERE platform=? AND external_id=?", (plat, it["external_id"]), one=True)
    gk = it.get("group_key") or analytics.norm_key(it.get("title") or "")
    attrs = json.dumps(it.get("attrs") or {}, ensure_ascii=False)
    if ex:
        pid = ex["id"]
        if it.get("seller") or it.get("attrs"): db.ex("UPDATE products SET seller=COALESCE(?,seller), attrs=? WHERE id=?", (it.get("seller"), attrs, pid))
    else:
        from app.domains.team import usage as _U
        try: _U.check("skus")
        except _U.QuotaExceeded as e: raise HTTPException(429, str(e))
        pid = db.ex("INSERT INTO products(source_id,platform,external_id,title,category,chain,brand,url,currency,cost,group_key,seller,attrs) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (src_id, plat, it["external_id"], it.get("title"), it.get("category"), it.get("chain"), it.get("brand"), it.get("url"), it.get("currency") or "CNY", it.get("cost"), gk, it.get("seller"), attrs))
    if it.get("image_url") and not (ex and ex.get("phash")): _save_image(it["image_url"], pid)
    for k in ("gtin", "model", "shipping", "rendered"):
        if it.get(k) is not None: db.ex(f"UPDATE products SET {k}=? WHERE id=?", (it[k], pid))
    if it.get("brand") and not (ex and ex.get("brand")): db.ex("UPDATE products SET brand=? WHERE id=?", (it["brand"], pid))
    stock = None if it.get("in_stock") is None else int(bool(it["in_stock"]))
    if stock is not None:
        old = ex.get("in_stock") if ex else None; db.ex("UPDATE products SET in_stock=? WHERE id=?", (stock, pid))
        if old is not None and old != stock:
            db.ex("INSERT INTO alerts(product_id,msg) VALUES(?,?)", (pid, "恢复有货" if stock else "缺货/下架")); _fire("stock", pid=pid, in_stock=stock)
    for v in it.get("variants") or []:   # 多规格：每个规格作为子商品单独记录价格与库存
        sub = dict(it, variants=None, external_id=f"{it['external_id']}#{v.get('sku') or v['name']}", title=f"{it.get('title')} / {v['name']}", price=v.get("price"),
                   in_stock=v.get("in_stock"), group_key=f"{gk}|{v['name']}", variant=v["name"])
        cid = upsert(src_id, sub); db.ex("UPDATE products SET parent_id=?, variant=? WHERE id=?", (pid, v["name"], cid))
    if it.get("price") is not None:
        last = db.q("SELECT price FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)
        record_price(pid, it["price"], it.get("method") or "manual", in_stock=stock)
        if last and last["price"] and abs(it["price"] - last["price"]) / last["price"] >= 0.05:
            db.ex("INSERT INTO alerts(product_id,msg) VALUES(?,?)", (pid, f"价格变动 {last['price']} -> {it['price']}"))
    return pid

@app.get("/api/health")
def health():
    te, ie = search.engine_status() if hasattr(search, "engine_status") else (search.ENGINE, search.IMG_ENGINE)
    return {"ok": True, "text_engine": te, "image_engine": ie, "adapters": list(sources.REGISTRY), "platform_catalog": len(PLATFORMS), "confidence_migrated": MIGRATED}
@app.get("/api/adapters")
def adapters(): return {k: (v.__doc__ or "").strip() for k, v in sources.REGISTRY.items()}
PF_COLS = "id,slug,name,country,region,type,scope,domain,url,lang,currency,note,strategy,search_url,status,status_detail,http_code,latency_ms,checked_at,enabled,source_id"
@app.get("/api/platforms")
def platforms(q: str = "", country: str = "", type: str = "", region: str = "", status: str = "", strategy: str = "", enabled: int | None = None, limit: int = 50, offset: int = 0):
    sql, a = f"FROM platforms WHERE 1=1", []
    if q: sql += " AND (name LIKE ? OR domain LIKE ? OR note LIKE ? OR country LIKE ?)"; a += [f"%{q}%"] * 4
    for col, v in (("country", country), ("region", region), ("status", status)):
        if v: sql += f" AND {col}=?"; a.append(v)
    if type: sql += " AND type LIKE ?"; a.append(f"%{type}%")
    if strategy: sql += " AND strategy LIKE ?"; a.append(strategy + "%")
    if enabled is not None: sql += " AND enabled=?"; a.append(enabled)
    total = db.q("SELECT COUNT(*) n " + sql, a, one=True)["n"]
    return {"total": total, "items": db.q(f"SELECT {PF_COLS} " + sql + " ORDER BY region, country, id LIMIT ? OFFSET ?", a + [limit, offset])}
@app.get("/api/platforms/stats")
def platform_stats():
    g = lambda col: db.q(f"SELECT {col} k, COUNT(*) n FROM platforms GROUP BY {col} ORDER BY n DESC")
    return {"total": db.q("SELECT COUNT(*) n FROM platforms", one=True)["n"], "enabled": db.q("SELECT COUNT(*) n FROM platforms WHERE enabled=1", one=True)["n"],
            "by_status": g("status"), "by_strategy": db.q("SELECT CASE WHEN strategy LIKE '专属%' THEN '专属规则' ELSE strategy END k, COUNT(*) n FROM platforms GROUP BY k"),
            "checked": db.q("SELECT COUNT(*) n FROM platforms WHERE checked_at IS NOT NULL", one=True)["n"], "running": HC["running"], "last_run": HC["last"]}
@app.get("/api/platforms/tree")
def platform_tree():
    return {"regions": db.q("SELECT region k, COUNT(*) n, SUM(enabled) e FROM platforms GROUP BY region ORDER BY region"),
            "countries": db.q("SELECT region, country k, COUNT(*) n, SUM(enabled) e FROM platforms GROUP BY region, country ORDER BY region, country"),
            "types": db.q("SELECT type k, COUNT(*) n FROM platforms WHERE type<>'' GROUP BY type ORDER BY n DESC LIMIT 25")}
def _enable(pid, on=True):
    pf = db.q("SELECT * FROM platforms WHERE id=?", (pid,), one=True)
    if not pf: raise HTTPException(404, "平台不存在")
    if on:
        sid = pf["source_id"]
        if not sid or not db.q("SELECT id FROM sources WHERE id=?", (sid,), one=True):
            sid = db.ex("INSERT OR REPLACE INTO sources(name,type,config,enabled) VALUES(?,?,?,1)", (f"平台:{pf['name']}({pf['country']})", "platform",
                        json.dumps({"platform_slug": pf["slug"], "platform": pf["domain"] or pf["name"], "urls": [], "query": ""}, ensure_ascii=False)))
        else: db.ex("UPDATE sources SET enabled=1 WHERE id=?", (sid,))
        db.ex("UPDATE platforms SET enabled=1, source_id=? WHERE id=?", (sid, pid)); return {"ok": True, "source_id": sid}
    if pf["source_id"]: db.ex("UPDATE sources SET enabled=0 WHERE id=?", (pf["source_id"],))
    db.ex("UPDATE platforms SET enabled=0 WHERE id=?", (pid,)); return {"ok": True}
@app.post("/api/platforms/{pid}/enable")
def enable_platform(pid: int): return _enable(pid, True)
@app.post("/api/platforms/{pid}/disable")
def disable_platform(pid: int): return _enable(pid, False)
@app.post("/api/platforms/bulk_enable")
def bulk_enable(body: dict = Body(...)):
    ids = body.get("ids") or [r["id"] for r in db.q("SELECT id FROM platforms WHERE country=? AND domain<>''", (body.get("country", ""),))]
    return {"enabled": len([_enable(i, body.get("on", True)) for i in ids])}
HC = {"running": False, "last": None}
@app.post("/api/platforms/healthcheck")
def run_healthcheck(body: dict = Body(default={})):
    if HC["running"]: return {"ok": False, "message": "体检正在进行中"}
    def job():
        HC["running"] = True
        try: n = asyncio.run(healthcheck.run(body.get("concurrency", 40), body.get("rps", 20), body.get("timeout", 12), body.get("limit"), body.get("ids")))
        finally: HC["running"] = False
        HC["last"] = {"domains": n, "at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
    threading.Thread(target=job, daemon=True).start(); return {"ok": True, "message": "体检已在后台启动"}
@app.post("/api/sources")
def add_source(body: dict = Body(...)):
    if body["type"] not in sources.REGISTRY: raise HTTPException(400, f"未知类型，可选 {list(sources.REGISTRY)}")
    return {"id": db.ex("INSERT OR REPLACE INTO sources(name,type,config) VALUES(?,?,?)", (body["name"], body["type"], json.dumps(body.get("config", {}), ensure_ascii=False)))}
@app.get("/api/sources")
def list_sources(): return db.q("SELECT * FROM sources")
@app.put("/api/sources/{sid}")
def edit_source(sid: int, body: dict = Body(...)):
    if body["type"] not in sources.REGISTRY: raise HTTPException(400, "未知类型")
    db.ex("UPDATE sources SET name=?,type=?,config=?,enabled=? WHERE id=?", (body["name"], body["type"], json.dumps(body.get("config", {}), ensure_ascii=False), int(body.get("enabled", 1)), sid)); return {"ok": True}
@app.delete("/api/sources/{sid}")
def del_source(sid: int): db.ex("DELETE FROM sources WHERE id=?", (sid,)); return {"ok": True}
@app.post("/api/sources/test")
def test_source(body: dict = Body(...)):
    try: items = sources.make(body["type"], body.get("config", {})).fetch()
    except Exception as e: return {"ok": False, "error": f"测试失败: {e}"}
    return {"ok": True, "count": len(items), "sample": items[:5]}
@app.get("/api/dashboard")
def dashboard():
    one = lambda sql: list(db.q(sql, one=True).values())[0]
    return {"products": one("SELECT COUNT(*) FROM products"), "platforms": one("SELECT COUNT(DISTINCT platform) FROM products"), "sources": one("SELECT COUNT(*) FROM sources"),
            "prices": one("SELECT COUNT(*) FROM prices"), "alerts": one("SELECT COUNT(*) FROM alerts"),
            "by_category": db.q("SELECT COALESCE(category,'未分类') k, COUNT(*) n FROM products GROUP BY k ORDER BY n DESC"),
            "by_platform": db.q("SELECT platform k, COUNT(*) n FROM products GROUP BY k ORDER BY n DESC"),
            "recent_alerts": db.q("SELECT a.*, p.title FROM alerts a LEFT JOIN products p ON p.id=a.product_id ORDER BY a.id DESC LIMIT 5"),
            "catalog": len(PLATFORMS), "last_fetch": one("SELECT MAX(ts) FROM prices")}
@app.get("/api/products/{pid}")
def product(pid: int):
    p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not p: raise HTTPException(404, "商品不存在")
    p.pop("clip", None); p["attrs"] = json.loads(p.get("attrs") or "{}")
    lr = db.q("SELECT * FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)
    if lr: p["last_price"] = lr["price"]; p["last_conf"] = CF.fresh(_conf_of(lr), lr["ts"]); p["last_ts"] = lr["ts"]
    return p
@app.get("/api/platforms/facets")
def facets(): return {"countries": [r["country"] for r in db.q("SELECT DISTINCT country FROM platforms ORDER BY country")], "types": sorted({t for r in db.q("SELECT DISTINCT type FROM platforms") for t in (r["type"] or "").split("/") if t}),
                        "statuses": [r["status"] for r in db.q("SELECT DISTINCT status FROM platforms")], "regions": [r["region"] for r in db.q("SELECT DISTINCT region FROM platforms ORDER BY region")]}
@app.post("/api/sources/{sid}/fetch")
def fetch_source(sid: int):
    s = db.q("SELECT * FROM sources WHERE id=?", (sid,), one=True)
    if not s: raise HTTPException(404)
    from app.domains.team import usage as _U
    try: _U.check("collects")
    except _U.QuotaExceeded as e: raise HTTPException(429, str(e))
    try: items = sources.make(s["type"], json.loads(s["config"])).fetch()
    except Exception as e:
        bus.publish("collect.failed", source_id=sid, type=s["type"], error=str(e)); raise HTTPException(502, f"数据源抓取失败: {e}")
    before = (db.q("SELECT MAX(id) m FROM products", one=True) or {}).get("m") or 0
    ids = [upsert(sid, it) for it in items]
    if [i for i in ids if i and i > before]:
        from app.domains.dedup.service import on_new_products
        on_new_products([i for i in ids if i and i > before])
    bus.publish("collect.succeeded", source_id=sid, type=s["type"], items=len(items)); return {"fetched": len(items), "product_ids": ids}
def fetch_all(wait=True):
    """定时监控：每个数据源作为一个调度任务提交（按域名限速、失败退避重试、最终进死信）。"""
    from .v6 import submit_source_fetch, SCHED
    from app.core import tenancy
    jobs = []
    with tenancy.bypass(): wss = [r["id"] for r in db.q("SELECT id FROM workspaces ORDER BY id")] or [1]
    for ws in ([tenancy.current()] if wait else wss):   # 定时任务遍历所有工作区；接口调用只跑当前工作区
      with tenancy.use(ws):
        jobs += [submit_source_fetch(s["id"], priority=5) for s in db.q("SELECT id FROM sources WHERE enabled=1 AND NOT (type='platform' AND config LIKE '%\"urls\": []%' AND config LIKE '%\"query\": \"\"%')")]
    if wait: SCHED.wait(jobs, timeout=600)
    return jobs
@app.post("/api/monitor/run")
def monitor_run(): fetch_all(); return {"ok": True}
@app.post("/api/products")
def add_product(body: dict = Body(...)):
    body.setdefault("external_id", hashlib.md5(body["title"].encode()).hexdigest()[:10]); return {"id": upsert(None, body)}
@app.get("/api/products")
def products(category: str = "", chain: str = "", platform: str = "", q: str = ""):
    key = ("products", category, chain, platform, q)
    hit = _CACHE.get(key)
    if hit and hit[0] == db.VER[0]: return hit[1]
    out = _products(category, chain, platform, q); _CACHE[key] = (db.VER[0], out)
    if len(_CACHE) > 256: _CACHE.clear()
    return out
_CACHE = {}
def _products(category, chain, platform, q):
    sql = "SELECT p.* FROM products p WHERE p.merged_into IS NULL"; a = []
    for col, v in (("category", category), ("platform", platform)):
        if v: sql += f" AND {col}=?"; a.append(v)
    if chain: sql += " AND chain LIKE ?"; a.append(chain + "%")
    if q: sql += " AND (title LIKE ? OR brand LIKE ?)"; a += [f"%{q}%"] * 2
    latest = {r["product_id"]: r for r in db.q("SELECT * FROM prices WHERE id IN (SELECT MAX(id) FROM prices GROUP BY product_id)")}
    out = []
    for r in db.q(sql, a):
        r.pop("clip", None); r["attrs"] = json.loads(r.get("attrs") or "{}"); lr = latest.get(r["id"])
        r["last_price"] = lr["price"] if lr else None
        if lr: r["last_conf"] = CF.fresh(_conf_of(lr), lr["ts"]); r["last_ts"] = lr["ts"]
        out.append(r)
    return out
@app.post("/api/products/{pid}/prices")
def add_price(pid: int, body: dict = Body(...)):
    m = body.get("method", "manual")
    if m not in CF.METHOD: raise HTTPException(400, f"method 可选 {list(CF.METHOD)}")
    return {"ok": True, "conf": record_price(pid, float(body["price"]), m, body.get("ts"))}
@app.get("/api/products/{pid}/history")
def history(pid: int):
    h = db.q("SELECT * FROM prices WHERE product_id=? ORDER BY ts, id", (pid,))
    out = [{"id": r["id"], "price": r["price"], "ts": r["ts"], "method": r["method"], "conf": _conf_of(r)} for r in h]
    return {"history": out, "stats": analytics.history_stats(out)}
@app.get("/api/products/{pid}/forecast")
def forecast(pid: int, horizon: int = 7):
    h = db.q("SELECT price, ts FROM prices WHERE product_id=? ORDER BY ts, id", (pid,)); f = analytics.forecast(h, horizon)
    f["conf"] = CF.forecast_conf(__import__("numpy").array([x["price"] for x in h], float)); return f
def _peers(pid):
    p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not p: raise HTTPException(404)
    peers = []
    for r in db.q("SELECT id,platform,title,seller FROM products WHERE group_key=?", (p["group_key"],)):
        lr = db.q("SELECT * FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (r["id"],), one=True)
        r["price"] = lr and lr["price"]; r["conf"] = lr and CF.fresh(_conf_of(lr), lr["ts"]); peers.append(r)
    return p, peers
@app.get("/api/products/{pid}/compare")
def compare(pid: int):
    p, peers = _peers(pid); peers = sorted([x for x in peers if x["price"] is not None], key=lambda x: x["price"])
    return {"group_key": p["group_key"], "offers": peers, "cheapest": peers[0] if peers else None, "spread": round(peers[-1]["price"] - peers[0]["price"], 2) if peers else 0}
@app.get("/api/products/{pid}/pricing")
def pricing(pid: int, markup: float = 0.2, cost: float | None = None):
    p, peers = _peers(pid)
    comp = [x for x in peers if x["id"] != pid and x["price"]]
    r = analytics.pricing(cost or p["cost"], [x["price"] for x in comp], markup)
    r["conf"] = CF.pricing_conf(cost or p["cost"], [x["conf"]["confidence"] for x in comp]); return r
@app.get("/api/compare/deep")
def deep_compare(ids: str):
    """深度对比：ids=1,2,3（2-8 个）。逐项对比价格/统计/预测/平台/卖家/类目/属性，并标出差异与最优/最差。"""
    idl = [int(x) for x in ids.split(",") if x.strip()][:8]
    if len(idl) < 2: raise HTTPException(400, "至少选择 2 个商品")
    items = []
    for i in idl:
        p = product(i); h = history(i); f = forecast(i)
        items.append({"product": p, "history": h["history"], "stats": h["stats"], "forecast": f})
    fields = {"platform": "平台", "seller": "卖家", "category": "类目", "chain": "产业链", "brand": "品牌", "currency": "币种"}
    diff = {k: len({str(it["product"].get(k)) for it in items}) > 1 for k in fields}
    keys = sorted({k for it in items for k in it["product"]["attrs"]})
    attr_diff = {k: len({str(it["product"]["attrs"].get(k)) for it in items}) > 1 for k in keys}
    def best(vals, low=True):
        v = [(x, j) for j, x in enumerate(vals) if x is not None]
        if not v: return None, None
        return (min(v)[1], max(v)[1]) if low else (max(v)[1], min(v)[1])
    nums = {"last_price": [it["product"].get("last_price") for it in items], "min": [it["stats"].get("min") for it in items], "avg": [it["stats"].get("avg") for it in items],
            "max": [it["stats"].get("max") for it in items], "change_pct": [it["stats"].get("change_pct") for it in items],
            "forecast_7": [(it["forecast"].get("linear_regression") or [None])[-1] for it in items], "confidence": [(it["product"].get("last_conf") or {}).get("confidence") for it in items]}
    rank = {k: dict(zip(("best", "worst"), best(v, low=(k != "confidence")))) for k, v in nums.items()}
    return {"items": items, "fields": fields, "diff": diff, "attr_keys": keys, "attr_diff": attr_diff, "rank": rank,
            "same_group": len({it["product"]["group_key"] for it in items}) == 1}
@app.get("/api/alerts")
def alerts(): return db.q("SELECT a.*, p.title, p.platform FROM alerts a LEFT JOIN products p ON p.id=a.product_id ORDER BY a.id DESC LIMIT 200")
@app.get("/api/search/chain")
def s_chain(chain: str): return products(chain=chain)
@app.get("/api/search/category")
def s_cat(category: str): return products(category=category)
@app.get("/api/search/keyword")
def s_kw(q: str): return products(q=q)
@app.get("/api/search/semantic")
def s_sem(q: str, k: int = 10, min_score: float = 0.0): r = search.semantic(q, products(), k, min_score, key=("all", db.VER[0])); return {"engine": search.ENGINE, "vector_engine": getattr(search, "vector_engine_now", lambda: None)(), "results": r}
@app.post("/api/search/image")
async def s_img(file: UploadFile = File(...), k: int = 10):
    t = tempfile.NamedTemporaryFile(delete=False, suffix=".img"); t.write(await file.read()); t.close()
    full = {p["id"]: p for p in products()}
    res = search.image_search(search.image_features(t.name), db.q("SELECT * FROM products"), k)
    return {"engine": search.IMG_ENGINE, "results": [dict(full.get(r["id"], {}), score=r["score"], method=r["method"]) for r in res]}
@app.post("/api/products/{pid}/image")
async def set_img(pid: int, file: UploadFile = File(...)):
    path = os.path.join(IMG_DIR, f"{pid}.img"); open(path, "wb").write(await file.read()); _save_image(path, pid); return {"ok": True}
@app.get("/api/search/ai")
def s_ai(q: str, provider: str = "deepseek"):
    r = ai_search.ai_search(provider, q, 10)
    for it in r.get("results", []):
        try: price = float(it.get("price"))
        except Exception: price = None
        c = CF.score_price(price, "ai", currency="CNY", title=it.get("title")); c["basis"].append(f"来自 {provider} 第 {it.get('rank')} 名，需二次核验"); it["conf"] = c
    return r
@app.get("/api/ai/providers")
def providers(): return {k: {"env": v["env"], "configured": bool(os.environ.get(v["env"]))} for k, v in ai_search.PROVIDERS.items()}
@app.get("/")
def index(): return FileResponse(os.path.join(HERE, "static", "index.html"))
sched = BackgroundScheduler()
def _warm_search_models():
    """v14：/readyz 不再被模型拖住；后台预热 ST/CLIP，首搜不必干等。"""
    try:
        if hasattr(search, "_ensure_st"):
            search._ensure_st()
        if hasattr(search, "_ensure_clip"):
            search._ensure_clip()
    except Exception as e:
        print("warm search models:", e)
@app.on_event("startup")
def _start():
    sched.add_job(fetch_all, "interval", kwargs={"wait": False}, minutes=int(os.environ.get("PRICEHUB_MONITOR_MIN", "60")), id="monitor"); sched.start()
    if os.environ.get("PRICEHUB_WARM_ST", "1") == "1":
        threading.Thread(target=_warm_search_models, daemon=True, name="warm-search").start()

from . import v4  # noqa: E402  v4 体验优化：引导、任务队列、一键监控、撤销、建议
from . import v5  # noqa: E402  v5 全生命周期补全
from .api import team
from .api import official  # noqa: F401  官方数据源适配器
from .api import v8_platform  # noqa: F401  渲染池/扩展/pgvector  # noqa: E402,F401  v7 团队协作（须在 v6 之前导入，使版本中间件保持最外层）
from .api import backtest as _bt  # noqa: E402,F401  v9 预测回测
from . import v6  # noqa: E402  v6 平台化：插件 SDK、调度器、事件总线、指标、API 版本化
from .api import nodes as _nodes  # noqa: E402,F401  v10 分布式采集节点（须在 v6/platform 之后导入）
from .api import dedup as _dedup  # noqa: E402,F401  v12 同款去重
