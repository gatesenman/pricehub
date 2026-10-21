"""定价领域：MAP 控价、取证、卖家画像、定价规则与利润模拟（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401
from app.domains.common import EVID_DIR, _latest
from app.domains.alerting.service import _map_of, send

def map_prices(): return db.q("SELECT * FROM map_prices ORDER BY group_key")
def set_map(body: dict = Body(...)):
    if body.get("price") in (None, ""): db.ex("DELETE FROM map_prices WHERE group_key=?", (body["group_key"],))
    else: db.ex("INSERT OR REPLACE INTO map_prices(group_key,price,note) VALUES(?,?,?)", (body["group_key"], float(body["price"]), body.get("note", "")))
    return map_prices()
def map_violations():
    out = []
    for p in products():
        m = p.get("map_price") or (db.q("SELECT price FROM map_prices WHERE group_key=?", (p["group_key"],), one=True) or {}).get("price")
        if m and p.get("last_price") is not None and p["last_price"] < m:
            ev = db.q("SELECT id, ts FROM evidence WHERE product_id=? ORDER BY id DESC LIMIT 1", (p["id"],), one=True)
            out.append({"id": p["id"], "title": p["title"], "platform": p["platform"], "seller": p.get("seller") or p["platform"], "url": p.get("url"), "price": p["last_price"], "map": m,
                        "gap_pct": round((1 - p["last_price"] / m) * 100, 1), "currency": p.get("currency"), "evidence": ev})
    return sorted(out, key=lambda x: -x["gap_pct"])
def capture_evidence(pid, prog=lambda *a: None):
    p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not p: raise HTTPException(404, "商品不存在")
    lp = _latest(pid); url = p.get("url")
    path = os.path.abspath(os.path.join(EVID_DIR, f"{pid}_{int(time.time())}.png"))
    if url and url.startswith("http"):
        prog(0.3, "无头浏览器打开商品页并整页截图…"); r = render.render(url, screenshot=path)
    else:   # 没有商品页时生成一张记录卡片截图（价格、时间、来源），保证有据可查
        prog(0.3, "商品没有网页链接，生成记录卡片…")
        html = f"<html><body style='font-family:sans-serif;padding:30px'><h2>PriceHub 价格取证记录</h2><p>商品：{p['title']}</p><p>平台：{p['platform']} 卖家：{p.get('seller') or '-'}</p><p>价格：{lp and lp['price']} {p['currency']}</p><p>记录时间(UTC)：{lp and lp['ts']}</p></body></html>"
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False); tmp.write(html.encode()); tmp.close(); render.render("file://" + tmp.name, screenshot=path)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    eid = db.ex("INSERT INTO evidence(product_id,price,map_price,url,path,sha256) VALUES(?,?,?,?,?,?)", (pid, lp and lp["price"], _map_of(p), url, path, sha))
    return {"evidence_id": eid, "path": path, "sha256": sha, "product_id": pid}
def evidence_task(pid: int): return submit("evidence", f"取证截图 #{pid}", lambda prog: capture_evidence(pid, prog))
def evidence_list(): return db.q("SELECT e.*, p.title, p.platform, p.seller FROM evidence e LEFT JOIN products p ON p.id=e.product_id ORDER BY e.id DESC")
def evidence_image(eid: int):
    e = db.q("SELECT path FROM evidence WHERE id=?", (eid,), one=True)
    if not e or not os.path.exists(e["path"]): raise HTTPException(404, "截图不存在")
    return FileResponse(e["path"], media_type="image/png")
def sellers():
    ps = products(); med = {}
    for p in ps:
        if p.get("last_price") is not None: med.setdefault(p["group_key"], []).append(p["last_price"])
    viol = {v["id"] for v in map_violations()}; out = {}
    for p in ps:
        s = out.setdefault(p.get("seller") or p["platform"], {"seller": p.get("seller") or p["platform"], "platforms": set(), "products": 0, "idx": [], "violations": 0, "out_of_stock": 0})
        s["platforms"].add(p["platform"]); s["products"] += 1; s["violations"] += p["id"] in viol; s["out_of_stock"] += p.get("in_stock") == 0
        g = med.get(p["group_key"]) or []
        if p.get("last_price") is not None and len(g) > 1: s["idx"].append(p["last_price"] / float(np.median(g)) * 100)
    res = []
    for s in out.values():
        idx = round(float(np.mean(s["idx"])), 1) if s["idx"] else None
        tag = "低价/乱价风险" if s["violations"] else ("价格偏低" if idx and idx < 97 else ("价格偏高" if idx and idx > 103 else "正常"))
        res.append({**s, "platforms": sorted(s["platforms"]), "price_index": idx, "idx": None, "tag": tag})
    return sorted(res, key=lambda x: (-x["violations"], x["price_index"] or 100))
STRATEGIES = {"beat_lowest": "比竞品最低价再低 X%", "match_lowest": "跟竞品最低价持平", "median": "对齐市场中位数", "cost_plus": "成本加成 X%", "premium": "比竞品均价高 X%"}
def pricing_rules(): return {"strategies": STRATEGIES, "items": db.q("SELECT * FROM pricing_rules ORDER BY id")}
def add_pricing_rule(body: dict = Body(...)):
    if body.get("strategy") not in STRATEGIES: raise HTTPException(400, "未知的定价策略")
    rid = db.ex("INSERT INTO pricing_rules(name,strategy,param,min_margin,floor,ceiling,respect_map,round_to,category) VALUES(?,?,?,?,?,?,?,?,?)",
                (body.get("name") or STRATEGIES[body["strategy"]], body["strategy"], float(body.get("param") or 0), float(body.get("min_margin", 0.1)), body.get("floor"), body.get("ceiling"),
                 int(body.get("respect_map", 1)), body.get("round_to", ""), body.get("category")))
    return db.q("SELECT * FROM pricing_rules WHERE id=?", (rid,), one=True)
def del_pricing_rule(rid: int): db.ex("DELETE FROM pricing_rules WHERE id=?", (rid,)); return {"ok": True}
def apply_rule(rule, p, comps):
    st, x, why = rule["strategy"], float(rule.get("param") or 0) / 100, []
    cost = p.get("cost"); lo = min(comps) if comps else None; med = float(np.median(comps)) if comps else None; avg = float(np.mean(comps)) if comps else None
    base = {"beat_lowest": lo and lo * (1 - x), "match_lowest": lo, "median": med, "premium": avg and avg * (1 + x), "cost_plus": cost and cost * (1 + (x or 0.2))}[st]
    if base is None: base = cost * 1.2 if cost else p.get("last_price"); why.append("缺少竞品或成本数据，按现价/成本兜底")
    else: why.append(f"{STRATEGIES[st].replace('X', str(rule.get('param') or 0))}：{base:.2f}")
    price = base
    if cost and rule.get("min_margin") is not None and price < cost * (1 + rule["min_margin"]): price = cost * (1 + rule["min_margin"]); why.append(f"保底毛利 {rule['min_margin']:.0%}：抬到 {price:.2f}")
    if rule.get("floor") and price < rule["floor"]: price = rule["floor"]; why.append(f"不低于下限 {rule['floor']}")
    if rule.get("ceiling") and price > rule["ceiling"]: price = rule["ceiling"]; why.append(f"不高于上限 {rule['ceiling']}")
    m = _map_of(p)
    if rule.get("respect_map", 1) and m and price < m: price = m; why.append(f"遵守最低限价 {m}")
    if rule.get("round_to") == ".9": price = float(np.floor(price)) + 0.9 if price % 1 <= 0.9 else float(np.floor(price)) + 1.9; why.append("尾数取 .9")
    elif rule.get("round_to") == "int": price = float(round(price)); why.append("取整")
    return round(price, 2), why
def simulate(body: dict = Body(...)):
    """利润模拟：销量 = 基准销量 × (新价/现价)^(-弹性)；利润 = (价格×(1-平台费率) - 成本) × 销量。"""
    rule = body.get("rule") or (db.q("SELECT * FROM pricing_rules WHERE id=?", (body.get("rule_id"),), one=True) if body.get("rule_id") else None)
    if not rule: raise HTTPException(400, "请选择或填写定价规则")
    fee, el, vol = float(body.get("fee_rate", 0.05)), float(body.get("elasticity", 1.5)), float(body.get("base_volume", 100))
    ids = body.get("ids") or [p["id"] for p in products() if not rule.get("category") or p.get("category") == rule.get("category")]
    rows, tot0, tot1 = [], 0.0, 0.0
    for pid in ids[:500]:
        p = product(pid); cur = p.get("last_price")
        if cur is None: continue
        comps = [x["price"] for x in db.q("SELECT (SELECT price FROM prices WHERE product_id=p.id ORDER BY id DESC LIMIT 1) price FROM products p WHERE group_key=? AND id<>?", (p["group_key"], pid)) if x["price"]]
        new, why = apply_rule(rule, p, comps); cost = p.get("cost") or 0
        v1 = vol * (new / cur) ** (-el) if cur else vol
        pr0, pr1 = (cur * (1 - fee) - cost) * vol, (new * (1 - fee) - cost) * v1; tot0 += pr0; tot1 += pr1
        rows.append({"id": pid, "title": p["title"], "platform": p["platform"], "current": cur, "suggested": new, "change_pct": round((new / cur - 1) * 100, 2), "cost": p.get("cost"),
                     "margin_pct": round((new * (1 - fee) - cost) / new * 100, 1) if new and cost else None, "volume": round(v1, 1), "profit_now": round(pr0, 2), "profit_new": round(pr1, 2), "why": why, "competitors": len(comps)})
    return {"rule": rule, "assumptions": {"fee_rate": fee, "elasticity": el, "base_volume": vol}, "rows": rows, "profit_now": round(tot0, 2), "profit_new": round(tot1, 2),
            "delta": round(tot1 - tot0, 2), "note": "利润为按价格弹性估算的模拟值，仅供决策参考"}
def pricing_push(body: dict = Body(...)):
    """执行：把调价建议推送给订阅了 price_suggestion 事件的 Webhook（如自家 ERP），不直接改平台价格。"""
    r = simulate(body); n = 0
    chs = db.q("SELECT * FROM channels WHERE enabled=1 AND type='webhook' AND config LIKE '%price_suggestion%'")
    if not chs: raise HTTPException(400, "还没有订阅「调价建议」事件的 Webhook 渠道：在「告警与通知」新建 Webhook 渠道并勾选该事件")
    for ch in chs:
        ok, _ = send(ch, "调价建议", f"{len(r['rows'])} 个商品的调价建议", {"event": "price_suggestion", "items": [{k: x[k] for k in ("id", "title", "current", "suggested")} for x in r["rows"]]}, event="price_suggestion"); n += ok
    return {"ok": n > 0, "channels": n, "items": len(r["rows"])}
