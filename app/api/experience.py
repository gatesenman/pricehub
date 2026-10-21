"""v4 体验优化接口：首次引导与设置、后台任务队列、一键监控（粘贴链接）、批量操作与撤销、全局搜索建议、平台不可用原因说明。"""
import os, json, time, uuid, threading, asyncio, hashlib, re, datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import httpx
from fastapi import HTTPException, Body
from app import db, sources, parsing, healthcheck, ai_search
from app.main import app, upsert, products, HC

# ---------------- 设置 / 首次引导 ----------------
def get_setting(k, default=None):
    r = db.q("SELECT v FROM settings WHERE k=?", (k,), one=True)
    return json.loads(r["v"]) if r else default
def set_setting(k, v): db.ex("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
def _apply_keys():
    for prov, key in (get_setting("ai_keys", {}) or {}).items():
        cfg = ai_search.PROVIDERS.get(prov)
        if cfg and key: os.environ[cfg["env"]] = key
_apply_keys()
def _mask(k): return (k[:3] + "••••" + k[-4:]) if k and len(k) > 8 else ("••••" if k else "")
@app.get("/api/settings")
def settings():
    keys = get_setting("ai_keys", {}) or {}
    return {"onboarded": bool(get_setting("onboarded", False)) or os.environ.get("PRICEHUB_ONBOARDED") == "1",
            "countries": get_setting("countries", []), "ai_provider": get_setting("ai_provider", "deepseek"),
            "ai_keys": {p: {"set": bool(keys.get(p) or os.environ.get(c["env"])), "masked": _mask(keys.get(p) or os.environ.get(c["env"]) or "")} for p, c in ai_search.PROVIDERS.items()}}
@app.post("/api/settings")
def save_settings(body: dict = Body(...)):
    for k in ("onboarded", "countries", "ai_provider"):
        if k in body: set_setting(k, body[k])
    if "ai_key" in body:
        prov = body.get("ai_provider") or get_setting("ai_provider", "deepseek")
        if prov not in ai_search.PROVIDERS: raise HTTPException(400, "未知的 AI 服务商")
        keys = get_setting("ai_keys", {}) or {}; key = (body["ai_key"] or "").strip()
        if key: keys[prov] = key; os.environ[ai_search.PROVIDERS[prov]["env"]] = key
        else: keys.pop(prov, None); os.environ.pop(ai_search.PROVIDERS[prov]["env"], None)
        set_setting("ai_keys", keys)
    return settings()
@app.get("/api/onboarding/countries")
def ob_countries():
    return db.q("SELECT country k, MIN(region) region, COUNT(*) n, SUM(CASE WHEN status IN ('解析成功','可达') THEN 1 ELSE 0 END) ok, SUM(enabled) e "
                "FROM platforms WHERE domain<>'' GROUP BY country ORDER BY n DESC, MIN(id)")

# ---------------- 后台任务队列 ----------------
TASKS = {}; _POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="task")
def _now(): return time.time()
def submit(kind, title, fn, **meta):
    tid = uuid.uuid4().hex[:10]
    t = TASKS[tid] = {"id": tid, "kind": kind, "title": title, "status": "排队中", "progress": 0.0, "message": "", "result": None,
                      "error": None, "advice": None, "created": _now(), "finished": None, **meta}
    def prog(p, msg=""): t["progress"] = round(max(0.0, min(1.0, p)), 3); t["message"] = msg or t["message"]
    def run():
        t["status"] = "进行中"
        try: t["result"] = fn(prog); t["status"] = "已完成"; t["progress"] = 1.0
        except FriendlyError as e: t["status"] = "失败"; t["error"] = str(e); t["advice"] = e.advice; t["result"] = e.result
        except Exception as e: t["status"] = "失败"; t["error"] = f"{e}"
        t["finished"] = _now()
    from app.core import tenancy as _tn; _POOL.submit(_tn.wrap(run))   # 带上当前工作区
    if len(TASKS) > 200:
        for k in sorted(TASKS, key=lambda k: TASKS[k]["created"])[:50]: TASKS.pop(k, None)
    return t
class FriendlyError(Exception):
    def __init__(self, msg, advice=None, result=None): super().__init__(msg); self.advice = advice; self.result = result
@app.get("/api/tasks")
def tasks(): return sorted(TASKS.values(), key=lambda t: -t["created"])[:50]
@app.get("/api/tasks/{tid}")
def task(tid: str):
    if tid not in TASKS: raise HTTPException(404, "任务不存在或已过期")
    return TASKS[tid]
@app.delete("/api/tasks")
def clear_tasks():
    for k in [k for k, t in TASKS.items() if t["status"] in ("已完成", "失败")]: TASKS.pop(k)
    return {"ok": True}

def _fetch_one(sid):
    from app.main import fetch_source
    return fetch_source(sid)
@app.post("/api/tasks")
def new_task(body: dict = Body(...)):
    kind = body.get("kind")
    if kind == "fetch_source":
        sid = int(body["source_id"]); s = db.q("SELECT name FROM sources WHERE id=?", (sid,), one=True)
        if not s: raise HTTPException(404, "数据源不存在")
        def fn(prog):
            prog(0.2, "正在抓取…")
            try: r = _fetch_one(sid)
            except HTTPException as e: raise FriendlyError(str(e.detail).replace("数据源抓取失败: ", ""), explain_error(str(e.detail)))
            return {"fetched": r["fetched"], "product_ids": r["product_ids"]}
        return submit(kind, f"抓取「{s['name']}」", fn)
    if kind == "monitor_all":
        def fn(prog):
            ss = db.q("SELECT id,name FROM sources WHERE enabled=1 AND NOT (type='platform' AND config LIKE '%\"urls\": []%' AND config LIKE '%\"query\": \"\"%')")
            ok = fail = n = 0; errs = []
            for i, s in enumerate(ss):
                prog(i / max(1, len(ss)), f"{i + 1}/{len(ss)} {s['name']}")
                try: n += _fetch_one(s["id"])["fetched"]; ok += 1
                except Exception as e: fail += 1; errs.append(f"{s['name']}：{getattr(e, 'detail', e)}")
            return {"sources": len(ss), "ok": ok, "failed": fail, "fetched": n, "errors": errs[:10]}
        return submit(kind, "全部数据源立即抓取", fn)
    if kind == "healthcheck":
        if HC["running"]: raise HTTPException(409, "平台体检已在进行中，请稍候")
        def fn(prog):
            HC["running"] = True
            try: n = asyncio.run(healthcheck.run(body.get("concurrency", 40), body.get("rps", 20), body.get("timeout", 12), body.get("limit"), body.get("ids"),
                                                progress=lambda d, tot: prog(d / max(1, tot), f"已检查 {d}/{tot} 个网站")))
            finally: HC["running"] = False
            HC["last"] = {"domains": n, "at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
            return {"domains": n}
        return submit(kind, "平台连通性体检", fn)
    if kind == "quick_monitor": return quick_monitor(body)
    raise HTTPException(400, "未知任务类型")

# ---------------- 平台不可用：原因与下一步 ----------------
EXPLAIN = {
  "解析成功": ("可以正常读取价格", "该平台网页提供了可识别的价格信息。", ["直接粘贴该平台的商品链接即可开始监控"]),
  "可达": ("能打开，但首页未发现价格", "网站可以访问，但首页/搜索页没有标准化的价格信息，具体商品页通常仍可读取。", ["粘贴一个具体商品页链接试试", "如果仍读不到价格，可在数据源中为它设置价格位置规则"]),
  "需JS渲染": ("页面需要浏览器脚本才能显示", "该网站的商品和价格由浏览器脚本动态加载，直接读取网页拿不到价格。", ["优先使用该平台的开放接口或第三方数据服务（新建数据源 > 数据接口）", "也可以定期导出表格后用「导入 CSV」上传"]),
  "反爬拦截": ("网站拦截了自动访问", "该平台启用了机器人校验或访问频率限制（验证码、安全验证页等），自动读取会被拦下。", ["使用平台官方开放接口或授权的数据服务", "改为手动录入价格或定期导入表格", "稍后再试：部分拦截是临时性的"]),
  "不可达": ("暂时无法连接", "网站当前无法打开（服务器错误、超时或所在地区无法访问）。", ["稍后在平台目录里重新「体检」", "检查网络或代理设置", "确认该平台是否已更换域名"]),
  "域名失效": ("网址已失效", "该网站的域名已无法解析，平台可能已更名或停止运营。", ["在平台目录中搜索它的新名称或新域名", "改用同类平台监控"]),
  "已关停": ("平台已停止运营", "名录标注该平台已关停，无法再获取价格。", ["改用同一国家/地区的同类平台"]),
  "无网址": ("缺少网址", "名录中这个平台没有可用网址，暂时无法自动读取。", ["如知道它的网址，可新建网页数据源手动添加"]),
  None: ("还没有检查过", "这个平台尚未做连通性检查，不确定能否读取价格。", ["在平台目录点击「体检」检查它"]),
}
def explain(status, detail=None):
    t, why, nxt = EXPLAIN.get(status, EXPLAIN[None])
    return {"status": status or "未检查", "title": t, "reason": why + (f"（检测详情：{detail}）" if detail else ""), "next": nxt}
def explain_error(msg):
    m = msg or ""
    if "反爬" in m: return explain("反爬拦截", m)
    if "需JS" in m: return explain("需JS渲染", m)
    if any(k in m for k in ("不可达", "timed out", "ConnectError", "Timeout", "Name or service")): return explain("不可达", m)
    if "未解析到价格" in m or "无结果" in m: return explain("可达", m)
    return {"status": "失败", "title": "读取失败", "reason": m, "next": ["检查链接是否是具体的商品页", "稍后重试，或改用手动录入价格"]}
@app.get("/api/platforms/{pid}/explain")
def platform_explain(pid: int):
    pf = db.q("SELECT status,status_detail,domain FROM platforms WHERE id=?", (pid,), one=True)
    if not pf: raise HTTPException(404, "平台不存在")
    return explain(pf["status"] if pf["domain"] else "无网址", pf["status_detail"])

# ---------------- 一键监控：粘贴商品链接 ----------------
def match_platform(url):
    host = (urlparse(url).hostname or "").lower()
    if not host: return None
    h = host[4:] if host.startswith("www.") else host
    parts = h.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        pf = db.q("SELECT * FROM platforms WHERE domain=? OR domain=? ORDER BY id LIMIT 1", (cand, "www." + cand), one=True)
        if pf: return pf
    return None
def _monitor_source(pf, host):
    """每个平台一个「一键监控」数据源，链接追加到 urls 里，之后定时监控自动覆盖。"""
    name = f"一键监控:{pf['name'] if pf else host}"
    s = db.q("SELECT * FROM sources WHERE name=?", (name,), one=True)
    if s: return s["id"], json.loads(s["config"])
    cfg = {"platform_slug": pf["slug"] if pf else "", "platform": (pf and (pf["domain"] or pf["name"])) or host, "urls": [], "query": ""}
    return db.ex("INSERT INTO sources(name,type,config,enabled) VALUES(?,?,?,1)", (name, "platform", json.dumps(cfg, ensure_ascii=False))), cfg
def quick_monitor(body):
    url = (body.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I): raise HTTPException(400, "请粘贴以 http:// 或 https:// 开头的商品链接")
    pf = match_platform(url); host = urlparse(url).hostname
    def fn(prog):
        prog(0.15, f"识别平台：{pf['name'] if pf else host}")
        from app import collect
        prog(0.35, "读取页面（必要时用无头浏览器渲染）…")
        d, st, why, code = collect.fetch_parse(url, json.loads((pf and pf["rules"]) or "{}"), (pf and pf["lang"]) or "zh-CN,en")
        if not d or not d.get("price"):
            if code == 404: raise FriendlyError("链接指向的页面不存在（404）", {"status": "失败", "title": "页面不存在", "reason": "这个链接打开后显示“页面不存在”，可能已下架或链接不完整。", "next": ["回到商品页重新复制完整链接"]})
            if st == "不可达" and not code: raise FriendlyError("无法打开这个链接", explain("不可达", why), {"platform": pf and pf["name"]})
            st2 = st if st != "解析成功" else "可达"
            raise FriendlyError(f"没能从这个页面读到价格（{EXPLAIN.get(st2, EXPLAIN[None])[0]}）", explain(st2, why), {"platform": pf and pf["name"]})
        prog(0.8, "保存并加入监控…")
        sid, cfg = _monitor_source(pf, host)
        if url not in cfg["urls"]:
            cfg["urls"].append(url); db.ex("UPDATE sources SET config=?, enabled=1 WHERE id=?", (json.dumps(cfg, ensure_ascii=False), sid))
        d.update(platform=(pf and (pf["domain"] or pf["name"])) or host, currency=d.get("currency") or (pf and pf["currency"]) or "CNY",
                 category=body.get("category") or d.get("category") or "", external_id=hashlib.md5(url.encode()).hexdigest()[:10],
                 seller=d.get("seller") or (pf and pf["name"]) or host, url=url)
        pid = upsert(sid, d)
        if pf and not pf["enabled"]: db.ex("UPDATE platforms SET enabled=1 WHERE id=?", (pf["id"],))
        return {"product_id": pid, "title": d.get("title"), "price": d.get("price"), "currency": d.get("currency"), "method": d.get("method"),
                "platform": pf["name"] if pf else host, "source_id": sid, "known_platform": bool(pf), "rendered": bool(d.get("rendered")), "in_stock": d.get("in_stock")}
    return submit("quick_monitor", f"一键监控 {host}", fn, url=url)
@app.post("/api/quick-monitor")
def quick_monitor_api(body: dict = Body(...)): return quick_monitor(body)
@app.post("/api/monitor/quick")
def quick_monitor_alias(body: dict = Body(...)): return quick_monitor(body)  # 浏览器扩展兼容

# ---------------- 批量操作与撤销 ----------------
UNDO = {}
def _undo_token(label, restore):
    tok = uuid.uuid4().hex[:12]; UNDO[tok] = {"label": label, "restore": restore, "at": _now()}
    for k in [k for k, v in UNDO.items() if _now() - v["at"] > 600]: UNDO.pop(k)
    return tok
def _rows(sql, ids): return db.q(sql % ",".join("?" * len(ids)), ids)
def _reinsert(table, rows):
    for r in rows:
        cols = list(r); db.ex(f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [r[c] for c in cols])
@app.post("/api/products/bulk")
def products_bulk(body: dict = Body(...)):
    ids = [int(i) for i in body.get("ids") or []]; action = body.get("action")
    if not ids: raise HTTPException(400, "请先勾选商品")
    if action == "delete":
        ps = _rows("SELECT * FROM products WHERE id IN (%s)", ids); pr = _rows("SELECT * FROM prices WHERE product_id IN (%s)", ids); al = _rows("SELECT * FROM alerts WHERE product_id IN (%s)", ids)
        for t, c in (("prices", "product_id"), ("alerts", "product_id"), ("products", "id")): db.ex(f"DELETE FROM {t} WHERE {c} IN ({','.join('?' * len(ids))})", ids)
        tok = _undo_token(f"删除 {len(ps)} 个商品", lambda: (_reinsert("products", ps), _reinsert("prices", pr), _reinsert("alerts", al)))
        return {"ok": True, "count": len(ps), "undo": tok, "message": f"已删除 {len(ps)} 个商品"}
    if action in ("monitor", "unmonitor"):
        prev = _rows("SELECT id, monitored FROM products WHERE id IN (%s)", ids); on = 1 if action == "monitor" else 0
        db.ex(f"UPDATE products SET monitored=? WHERE id IN ({','.join('?' * len(ids))})", [on] + ids)
        def restore():
            for r in prev: db.ex("UPDATE products SET monitored=? WHERE id=?", (r["monitored"], r["id"]))
        return {"ok": True, "count": len(prev), "undo": _undo_token(("恢复" if on else "暂停") + f"监控 {len(prev)} 个商品", restore),
                "message": ("已恢复监控 " if on else "已暂停监控 ") + f"{len(prev)} 个商品"}
    if action == "category":
        prev = _rows("SELECT id, category FROM products WHERE id IN (%s)", ids)
        db.ex(f"UPDATE products SET category=? WHERE id IN ({','.join('?' * len(ids))})", [body.get("value", "")] + ids)
        def restore():
            for r in prev: db.ex("UPDATE products SET category=? WHERE id=?", (r["category"], r["id"]))
        return {"ok": True, "count": len(prev), "undo": _undo_token(f"修改 {len(prev)} 个商品类目", restore), "message": f"已把 {len(prev)} 个商品归入「{body.get('value', '')}」"}
    raise HTTPException(400, "未知的批量操作")
@app.delete("/api/sources/{sid}/undoable")
def del_source_undoable(sid: int):
    s = db.q("SELECT * FROM sources WHERE id=?", (sid,), one=True)
    if not s: raise HTTPException(404, "数据源不存在")
    db.ex("DELETE FROM sources WHERE id=?", (sid,))
    return {"ok": True, "undo": _undo_token(f"删除数据源「{s['name']}」", lambda: _reinsert("sources", [s])), "message": f"已删除数据源「{s['name']}」"}
@app.post("/api/undo/{tok}")
def undo(tok: str):
    u = UNDO.pop(tok, None)
    if not u: raise HTTPException(404, "撤销已过期（10 分钟内有效）")
    u["restore"](); return {"ok": True, "message": f"已撤销：{u['label']}"}

# ---------------- 全局搜索建议 ----------------
@app.get("/api/suggest")
def suggest(q: str = ""):
    q = q.strip()
    if not q: return {"q": q, "url": None, "products": [], "platforms": [], "categories": []}
    if re.match(r"^https?://", q, re.I):
        pf = match_platform(q)
        return {"q": q, "url": {"url": q, "platform": pf and {"id": pf["id"], "name": pf["name"], "status": pf["status"], "explain": explain(pf["status"], pf["status_detail"])}},
                "products": [], "platforms": [], "categories": []}
    low = q.lower(); ps = products()
    hits = [p for p in ps if low in (p["title"] or "").lower() or low in (p.get("brand") or "").lower()][:6]
    pfs = db.q("SELECT id,name,country,status,domain FROM platforms WHERE name LIKE ? OR domain LIKE ? ORDER BY CASE WHEN name LIKE ? THEN 0 ELSE 1 END, id LIMIT 5", (f"%{q}%", f"%{q}%", f"{q}%"))
    cats = sorted({p["category"] for p in ps if p.get("category") and low in p["category"].lower()})[:4]
    return {"q": q, "url": None, "categories": cats, "platforms": pfs,
            "products": [{"id": p["id"], "title": p["title"], "platform": p["platform"], "last_price": p.get("last_price"), "currency": p.get("currency")} for p in hits]}

# 启动后在后台预热语义向量索引，避免第一次搜索卡顿
def _warm():
    try:
        from app import search
        if search._ST: search.semantic("预热", products(), 1, key=("all", db.VER[0]))
    except Exception as e: print("warm failed", e)
threading.Thread(target=_warm, daemon=True).start()
