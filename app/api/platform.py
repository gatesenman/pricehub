"""v6 平台化接线：调度器 + 死信、插件 SDK 加载、事件总线指标、/metrics、健康检查、结构化日志、API 版本化。"""
import json, os, time, logging, urllib.parse
from fastapi import HTTPException, Body, Request
from fastapi.responses import Response
from app import db, sources, sdk, render
from app.main import app
from app.core.events import bus
from app.core.scheduler import Scheduler, PermanentError
from app.core import observability as obs
from app.core.apiver import ApiVersionMiddleware
log = obs.setup_logging()
# ---------------- 调度器 ----------------
def _dead_sink(j):
    db.ex("INSERT INTO dead_letters(kind,job_key,payload,error,attempts) VALUES(?,?,?,?,?)", (j.kind, j.key, json.dumps(j.payload, ensure_ascii=False), j.error, j.attempts))
    obs.DEAD.set((db.q("SELECT COUNT(*) n FROM dead_letters", one=True) or {}).get("n", 0))
def _on_result(j, result):
    obs.JOBS.labels(j.kind, result).inc(); bus.publish(f"job.{result}", job_id=j.id, kind=j.kind, key=j.key, attempts=j.attempts, error=j.error)
SCHED = Scheduler(workers=int(os.environ.get("PRICEHUB_WORKERS", "4")), rate=float(os.environ.get("PRICEHUB_DOMAIN_RPS", "1")), burst=float(os.environ.get("PRICEHUB_DOMAIN_BURST", "3")),
                  base_backoff=float(os.environ.get("PRICEHUB_BACKOFF_S", "2")), dead_sink=_dead_sink, on_result=_on_result)
def source_key(s):
    """限速 key：取数据源配置里第一个网址的域名；无网址时按数据源类型。"""
    try: cfg = json.loads(s.get("config") or "{}")
    except Exception: cfg = {}
    for u in [cfg.get("url"), cfg.get("base_url"), *(cfg.get("urls") or [])]:
        if u and "://" in str(u): return urllib.parse.urlparse(str(u)).netloc
    return cfg.get("platform") or s.get("type") or "default"
def _h_source_fetch(p):
    from app.main import upsert
    s = db.q("SELECT * FROM sources WHERE id=?", (p["source_id"],), one=True)
    if not s: raise PermanentError("数据源不存在")
    try: items = sources.make(s["type"], json.loads(s["config"] or "{}")).fetch()
    except (sdk.PluginError, KeyError, ValueError) as e:
        bus.publish("collect.failed", source_id=s["id"], type=s["type"], error=str(e)); raise PermanentError(str(e))
    except Exception as e:
        bus.publish("collect.failed", source_id=s["id"], type=s["type"], error=str(e)); raise
    ids = [upsert(s["id"], it) for it in items]
    bus.publish("collect.succeeded", source_id=s["id"], type=s["type"], items=len(items)); return {"fetched": len(items), "product_ids": ids}
SCHED.register_handler("source.fetch", _h_source_fetch)
def submit_source_fetch(sid, priority=5, max_attempts=4):
    s = db.q("SELECT * FROM sources WHERE id=?", (sid,), one=True) or {"type": "default"}
    return SCHED.submit("source.fetch", {"source_id": sid}, key=source_key(s), priority=priority, max_attempts=max_attempts)
@app.get("/api/scheduler")
def scheduler_stats():
    st = SCHED.stats(); st["dead_letters"] = (db.q("SELECT COUNT(*) n FROM dead_letters", one=True) or {}).get("n", 0); obs.QUEUE.set(st["ready"] + st["delayed"]); return st
@app.post("/api/scheduler/submit")
def scheduler_submit(body: dict = Body(...)):
    sid = int(body.get("source_id") or 0)
    if not db.q("SELECT id FROM sources WHERE id=?", (sid,), one=True): raise HTTPException(404, "数据源不存在")
    j = submit_source_fetch(sid, int(body.get("priority", 0)), int(body.get("max_attempts", 4)))
    if body.get("wait"): SCHED.wait([j], timeout=float(body.get("timeout", 60)))
    return j.view() | {"result": j.result}
@app.put("/api/scheduler/rate")
def scheduler_rate(body: dict = Body(...)):
    k, r = (body.get("key") or "").strip(), float(body.get("rps") or 0)
    if not k or r <= 0: raise HTTPException(400, "请填写域名和每秒请求数（大于 0）")
    SCHED.set_rate(k, r, body.get("burst")); return {"ok": True}
@app.get("/api/deadletters")
def deadletters(limit: int = 100): return db.q("SELECT * FROM dead_letters ORDER BY id DESC LIMIT ?", (limit,))
@app.post("/api/deadletters/{did}/retry")
def deadletter_retry(did: int):
    d = db.q("SELECT * FROM dead_letters WHERE id=?", (did,), one=True)
    if not d: raise HTTPException(404)
    j = SCHED.submit(d["kind"], json.loads(d["payload"] or "{}"), key=d["job_key"], priority=0)
    db.ex("DELETE FROM dead_letters WHERE id=?", (did,)); return j.view()
@app.delete("/api/deadletters/{did}")
def deadletter_del(did: int): db.ex("DELETE FROM dead_letters WHERE id=?", (did,)); return {"ok": True}
# ---------------- 插件 ----------------
sdk.reload()
@app.get("/api/plugins")
def plugins(): return sdk.catalog()
@app.post("/api/plugins/reload")
def plugins_reload(): return {"report": sdk.reload(), **sdk.catalog()}
@app.get("/api/plugins/{pid}")
def plugin_info(pid: str):
    p = sdk.PLUGINS.get(pid)
    if not p: raise HTTPException(404, "没有这个插件")
    return p
@app.post("/api/plugins/{pid}/test")
def plugin_test(pid: str, body: dict = Body(...)):
    if pid not in sdk.PLUGINS: raise HTTPException(404, "没有这个插件")
    try: items = sources.make(pid, body.get("config") or {}).fetch()
    except Exception as e: return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "count": len(items), "sample": items[:5]}
# ---------------- 事件与指标 ----------------
def _metric_events(topic, **kw):
    obs.EVENTS.labels(topic.split(".")[0] + "." + (topic.split(".")[1] if "." in topic else "")).inc()
    if topic == "collect.succeeded": obs.COLLECT.labels("ok", kw.get("type") or "").inc()
    elif topic == "collect.failed": obs.COLLECT.labels("fail", kw.get("type") or "").inc()
    elif topic == "notify.sent": obs.NOTIFY.labels(kw.get("channel_type") or "", str(bool(kw.get("ok"))).lower()).inc()
bus.subscribe("*", _metric_events, "observability.metrics")
@app.get("/api/events")
def events(): return bus.stats()
def _metrics_allowed(request: Request):
    """/metrics 访问控制：本机直接放行；其他来源需 Bearer 令牌（环境变量 PRICEHUB_METRICS_TOKEN）或任一有效 API 令牌（X-API-Key）。"""
    ip = request.client.host if request.client else ""
    if ip in ("127.0.0.1", "::1", "localhost", "testclient") and not os.environ.get("PRICEHUB_METRICS_STRICT"): return True
    auth = request.headers.get("authorization", ""); tok = os.environ.get("PRICEHUB_METRICS_TOKEN")
    if tok and auth == f"Bearer {tok}": return True
    key = request.headers.get("x-api-key")
    if key:
        import hashlib
        return bool(db.q("SELECT id FROM tokens WHERE token_hash=?", (hashlib.sha256(key.encode()).hexdigest(),), one=True))
    return False
@app.get("/metrics")
def metrics(request: Request):
    if not _metrics_allowed(request): return Response("需要指标访问令牌：请求头 Authorization: Bearer <PRICEHUB_METRICS_TOKEN> 或 X-API-Key\n", status_code=401, media_type="text/plain; charset=utf-8")
    try:
        obs.PRODUCTS.set(db.q("SELECT COUNT(*) n FROM products", one=True)["n"]); obs.PRICES.set(db.q("SELECT COUNT(*) n FROM prices", one=True)["n"])
        obs.DEAD.set(db.q("SELECT COUNT(*) n FROM dead_letters", one=True)["n"]); st = SCHED.stats(); obs.QUEUE.set(st["ready"] + st["delayed"])
    except Exception as e: log.warning("metrics refresh failed", extra={"error": str(e)})
    body, ct = obs.metrics_text(); return Response(body, media_type=ct)
@app.get("/healthz")
def healthz(): return {"status": "ok", "uptime_s": int(time.time() - obs.START)}
@app.get("/readyz")
def readyz():
    checks = {}
    try: db.q("SELECT 1 x", one=True); checks["database"] = {"ok": True, "backend": db.BACKEND}
    except Exception as e: checks["database"] = {"ok": False, "error": str(e)}
    checks["scheduler"] = {"ok": True, "running": SCHED.running, "workers": SCHED.workers_n}
    checks["renderer"] = {"ok": True, **render.status()}
    checks["plugins"] = {"ok": all(p["state"] == "active" for p in sdk.PLUGINS.values()), "count": len(sdk.PLUGINS)}
    from app import search
    checks["vector_index"] = {"ok": True, "engine": search.VECTOR_ENGINE}
    ok = checks["database"]["ok"]
    return Response(json.dumps({"status": "ready" if ok else "not_ready", "checks": checks}, ensure_ascii=False), status_code=200 if ok else 503, media_type="application/json")
@app.middleware("http")
async def _metrics_mw(request: Request, call_next):
    t0 = time.perf_counter(); status = 500
    try:
        resp = await call_next(request); status = resp.status_code; return resp
    finally:
        r = request.scope.get("route"); route = getattr(r, "path", None) or ("static" if not request.url.path.startswith("/api") else "unmatched")
        dt = time.perf_counter() - t0; obs.HTTP_REQ.labels(request.method, route, str(status)).inc(); obs.HTTP_LAT.labels(route).observe(dt)
        if status >= 500 or dt > 2: log.warning("slow or failed request", extra={"method": request.method, "route": route, "status": status, "ms": int(dt * 1000)})
app.add_middleware(ApiVersionMiddleware)   # 最外层：/api/v1/* → /api/*
log.info("v6 platform ready", extra={"plugins": list(sdk.PLUGINS), "db_backend": db.BACKEND})
# ---------------- 大规模分页 ----------------
@app.get("/api/catalog/page")
def catalog_page(offset: int = 0, limit: int = 50, platform: str = "", q: str = ""):
    """面向百万 SKU 的分页列表：只取一页商品，再按主键批量取这页的最新价，避免全表加载。"""
    limit = max(1, min(limit, 500)); where, a = ["1=1"], []
    if platform: where.append("platform=?"); a.append(platform)
    if q: where.append("(title LIKE ? OR brand LIKE ?)"); a += [f"%{q}%"] * 2
    w = " AND ".join(where)
    rows = db.q(f"SELECT id,platform,external_id,title,category,brand,currency,group_key,in_stock FROM products WHERE {w} ORDER BY id LIMIT ? OFFSET ?", (*a, limit, offset))
    if rows:
        ids = [r["id"] for r in rows]; ph = ",".join("?" * len(ids))
        last = {r["product_id"]: r for r in db.q(f"SELECT p.product_id, p.price, p.ts FROM prices p JOIN (SELECT product_id, MAX(id) mid FROM prices WHERE product_id IN ({ph}) GROUP BY product_id) m ON p.id=m.mid", ids)}
        for r in rows: lr = last.get(r["id"]) or {}; r["last_price"], r["last_ts"] = lr.get("price"), lr.get("ts")
    total, exact = db.count("products", w, a)   # 总数缓存 30 秒；PostgreSQL 大表无条件时用统计估算
    return {"total": total, "total_exact": exact, "offset": offset, "limit": limit, "items": rows}
