"""v5 价格全生命周期补全：汇率与到手价、告警规则与通知渠道、智能同款匹配、促销日历与买入建议、MAP 控价与取证、卖家画像、
定价规则引擎与利润模拟、报表与定时报告、API 令牌/角色/审计、备份恢复、代理池与渲染设置、书签一键收藏。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.main import app, products, product, HOOKS, sched, HERE
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
EVID_DIR = os.path.join(HERE, "..", "data", "evidence")
def _now(): return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
def _latest(pid): return db.q("SELECT * FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)

# ================= 汇率与到手价 =================
FX_DEFAULT = {"CNY": 1, "USD": 7.12, "EUR": 7.75, "GBP": 9.05, "JPY": 0.047, "KRW": 0.0052, "HKD": 0.91, "TWD": 0.22, "MOP": 0.88, "SGD": 5.45, "MYR": 1.62,
              "THB": 0.21, "IDR": 0.00045, "VND": 0.00028, "PHP": 0.124, "INR": 0.084, "AUD": 4.7, "NZD": 4.3, "CAD": 5.15, "MXN": 0.38, "BRL": 1.3, "RUB": 0.08,
              "TRY": 0.18, "AED": 1.94, "SAR": 1.9, "ZAR": 0.39, "CHF": 8.2, "SEK": 0.68, "NOK": 0.66, "DKK": 1.04, "PLN": 1.8, "CZK": 0.31, "KPW": 0.0079,
              "MNT": 0.0021, "PKR": 0.025, "BDT": 0.058, "EGP": 0.14, "NGN": 0.0045, "KES": 0.055, "CLP": 0.0074, "COP": 0.0017, "ARS": 0.0065, "PEN": 1.9, "ILS": 1.95}
def fx(): return {**FX_DEFAULT, **(get_setting("fx", {}) or {})}
def to_cny(v, cur):
    if v is None: return None
    r = fx().get((cur or "CNY").upper())
    return round(v * r, 2) if r else None
def landed(p, price=None):
    """到手价 = (标价 + 运费 - 优惠券/满减) × (1 + 税率)；同时给出折合人民币。"""
    price = price if price is not None else p.get("last_price")
    if price is None: return None
    ship, coupon, tax = p.get("shipping") or 0, p.get("coupon") or 0, p.get("tax_rate") or 0
    total = round(max(0.0, price + ship - coupon) * (1 + tax), 2); cur = p.get("currency") or "CNY"
    return {"price": price, "shipping": ship, "coupon": coupon, "tax_rate": tax, "landed": total, "currency": cur, "landed_cny": to_cny(total, cur),
            "rate": fx().get(cur.upper()), "formula": f"({price} + 运费 {ship} - 优惠 {coupon}) × (1 + 税率 {tax:.0%}) = {total} {cur}"}
@app.get("/api/fx")
def get_fx(): return {"base": "CNY", "rates": fx(), "custom": get_setting("fx", {}) or {}, "note": "1 单位外币 = 多少人民币；默认值为内置参考汇率，可按需覆盖"}
@app.post("/api/fx")
def set_fx(body: dict = Body(...)):
    cur = get_setting("fx", {}) or {}
    for k, v in (body.get("rates") or {}).items():
        if v in (None, ""): cur.pop(k.upper(), None)
        else: cur[k.upper()] = float(v)
    set_setting("fx", cur); return get_fx()
EDITABLE = ("shipping", "tax_rate", "coupon", "map_price", "target_price", "cost", "gtin", "model", "category", "brand")
@app.put("/api/products/{pid}/costs")
def set_costs(pid: int, body: dict = Body(...)):
    for k, v in body.items():
        if k in EDITABLE: db.ex(f"UPDATE products SET {k}=? WHERE id=?", (None if v in ("", None) else v, pid))
    if body.get("target_price") not in (None, ""):   # 设置目标价即自动创建一条目标价提醒规则
        if not db.q("SELECT id FROM rules WHERE type='target' AND product_id=?", (pid,), one=True):
            db.ex("INSERT INTO rules(name,type,value,product_id,channel_ids) VALUES(?,?,?,?,?)", (f"目标价提醒 #{pid}", "target", float(body["target_price"]), pid, json.dumps([c["id"] for c in db.q("SELECT id FROM channels WHERE enabled=1")])))
        else: db.ex("UPDATE rules SET value=?, enabled=1 WHERE type='target' AND product_id=?", (float(body["target_price"]), pid))
    return product_full(pid)
@app.get("/api/products/{pid}/full")
def product_full(pid: int):
    p = product(pid); p["landed"] = landed(p); p["variants"] = db.q("SELECT id,variant,title,in_stock,(SELECT price FROM prices WHERE product_id=products.id ORDER BY id DESC LIMIT 1) price FROM products WHERE parent_id=?", (pid,))
    p["map_effective"] = p.get("map_price") or (db.q("SELECT price FROM map_prices WHERE group_key=?", (p["group_key"],), one=True) or {}).get("price")
    p["rules"] = db.q("SELECT * FROM rules WHERE product_id=? OR (product_id IS NULL AND (group_key IS NULL OR group_key=?))", (pid, p["group_key"]))
    return p
@app.get("/api/compare/landed")
def compare_landed(group_key: str):
    out = []
    for r in db.q("SELECT * FROM products WHERE group_key=?", (group_key,)):
        lp = _latest(r["id"])
        if lp: r.pop("clip", None); out.append({**{k: r[k] for k in ("id", "platform", "title", "seller", "currency", "in_stock")}, **landed(r, lp["price"])})
    return sorted(out, key=lambda x: (x["landed_cny"] is None, x["landed_cny"] or 0))

# ================= 通知渠道 =================
CHANNEL_TYPES = {"webhook": "通用 Webhook（JSON）", "wecom": "企业微信群机器人", "feishu": "飞书群机器人", "dingtalk": "钉钉群机器人", "telegram": "Telegram 机器人", "email": "邮件（SMTP）"}
def _payload(ch, title, text, data):
    t = ch["type"]; full = f"【PriceHub】{title}\n{text}"
    if t == "wecom": return {"msgtype": "text", "text": {"content": full}}
    if t == "dingtalk": return {"msgtype": "text", "text": {"content": full}}
    if t == "feishu": return {"msg_type": "text", "content": {"text": full}}
    if t == "telegram": return {"chat_id": json.loads(ch["config"] or "{}").get("chat_id"), "text": full}
    return {"source": "pricehub", "title": title, "text": text, **data}
def send(ch, title, text, data=None, rule_id=None, event="alert"):
    data = data or {}; cfg = json.loads(ch.get("config") or "{}")
    try:
        if ch["type"] == "email":
            m = MIMEText(text, "plain", "utf-8"); m["Subject"] = f"[PriceHub] {title}"; m["From"] = cfg.get("from") or cfg.get("user") or "pricehub@localhost"; m["To"] = cfg.get("to") or ""
            with smtplib.SMTP(cfg.get("host", "localhost"), int(cfg.get("port", 25)), timeout=15) as s:
                if cfg.get("starttls"): s.starttls()
                if cfg.get("user"): s.login(cfg["user"], cfg.get("password", ""))
                s.sendmail(m["From"], [x.strip() for x in m["To"].split(",") if x.strip()], m.as_string())
            ok, detail = True, "已发送邮件"
        else:
            url = ch["url"].rstrip("/") + "/sendMessage" if ch["type"] == "telegram" else ch["url"]
            if cfg.get("secret") and ch["type"] == "webhook": data = {**data}
            r = httpx.post(url, json=_payload(ch, title, text, data), timeout=10, headers={"X-PriceHub-Event": event, **({"X-PriceHub-Signature": hashlib.sha256((cfg.get("secret", "") + title).encode()).hexdigest()} if cfg.get("secret") else {})})
            ok, detail = r.status_code < 300, f"HTTP {r.status_code} {r.text[:120]}"
    except Exception as e: ok, detail = False, f"{type(e).__name__}: {e}"
    db.ex("INSERT INTO notifications(channel_id,rule_id,event,ok,detail,payload) VALUES(?,?,?,?,?,?)", (ch["id"], rule_id, event, int(ok), detail, json.dumps({"title": title, "text": text}, ensure_ascii=False)))
    from app.core.events import bus as _bus; _bus.publish("notify.sent", channel_id=ch["id"], channel_type=ch["type"], ok=bool(ok), event=event, rule_id=rule_id)
    return ok, detail
@app.get("/api/channels")
def channels(): return {"types": CHANNEL_TYPES, "items": db.q("SELECT * FROM channels ORDER BY id")}
@app.post("/api/channels")
def add_channel(body: dict = Body(...)):
    if body.get("type") not in CHANNEL_TYPES: raise HTTPException(400, f"渠道类型可选：{'、'.join(CHANNEL_TYPES.values())}")
    if body["type"] != "email" and not re.match(r"^https?://", body.get("url") or ""): raise HTTPException(400, "请填写以 http(s):// 开头的机器人/Webhook 地址")
    cid = db.ex("INSERT INTO channels(name,type,url,config,enabled) VALUES(?,?,?,?,?)", (body.get("name") or CHANNEL_TYPES[body["type"]], body["type"], body.get("url", ""), json.dumps(body.get("config") or {}, ensure_ascii=False), int(body.get("enabled", 1))))
    return db.q("SELECT * FROM channels WHERE id=?", (cid,), one=True)
@app.delete("/api/channels/{cid}")
def del_channel(cid: int): db.ex("DELETE FROM channels WHERE id=?", (cid,)); return {"ok": True}
@app.post("/api/channels/{cid}/test")
def test_channel(cid: int):
    ch = db.q("SELECT * FROM channels WHERE id=?", (cid,), one=True)
    if not ch: raise HTTPException(404, "渠道不存在")
    ok, d = send(ch, "测试消息", "这是一条来自 PriceHub 的测试通知，收到说明渠道配置正确。", {"event": "test"}, event="test")
    return {"ok": ok, "detail": d}
@app.get("/api/notifications")
def notifications(limit: int = 100): return db.q("SELECT n.*, c.name channel FROM notifications n LEFT JOIN channels c ON c.id=n.channel_id ORDER BY n.id DESC LIMIT ?", (limit,))

# ================= 告警规则 =================
RULE_TYPES = {"target": "降到目标价（含）以下", "drop_pct": "单次降价超过 X%", "rise_pct": "单次涨价超过 X%", "below": "低于某个价格", "above": "高于某个价格",
              "stock": "库存变化（缺货/恢复有货）", "map": "低于最低限价（破价）", "any": "任何价格变动"}
@app.get("/api/rules")
def rules(): return {"types": RULE_TYPES, "items": db.q("SELECT r.*, p.title product FROM rules r LEFT JOIN products p ON p.id=r.product_id ORDER BY r.id DESC")}
@app.post("/api/rules")
def add_rule(body: dict = Body(...)):
    if body.get("type") not in RULE_TYPES: raise HTTPException(400, "未知的规则类型")
    if body["type"] not in ("stock", "map", "any") and body.get("value") in (None, ""): raise HTTPException(400, "请填写数值")
    rid = db.ex("INSERT INTO rules(name,type,value,product_id,group_key,category,channel_ids,cooldown_min,enabled) VALUES(?,?,?,?,?,?,?,?,1)",
                (body.get("name") or RULE_TYPES[body["type"]], body["type"], body.get("value"), body.get("product_id"), body.get("group_key"), body.get("category"),
                 json.dumps(body.get("channel_ids") or []), int(body.get("cooldown_min", 60))))
    return db.q("SELECT * FROM rules WHERE id=?", (rid,), one=True)
@app.put("/api/rules/{rid}")
def edit_rule(rid: int, body: dict = Body(...)):
    for k in ("name", "value", "enabled", "cooldown_min"):
        if k in body: db.ex(f"UPDATE rules SET {k}=? WHERE id=?", (body[k], rid))
    if "channel_ids" in body: db.ex("UPDATE rules SET channel_ids=? WHERE id=?", (json.dumps(body["channel_ids"]), rid))
    return {"ok": True}
@app.delete("/api/rules/{rid}")
def del_rule(rid: int): db.ex("DELETE FROM rules WHERE id=?", (rid,)); return {"ok": True}
def _map_of(p): return p.get("map_price") or (db.q("SELECT price FROM map_prices WHERE group_key=?", (p["group_key"],), one=True) or {}).get("price")
def _check(r, p, ev, price=None, prev=None, in_stock=None):
    t, v = r["type"], r["value"]
    if ev == "stock": return (f"{'恢复有货' if in_stock else '缺货/下架'}") if t == "stock" else None
    if price is None: return None
    if t == "target" and price <= v: return f"已降到目标价：{price}（目标 {v}）"
    if t == "below" and price < v: return f"价格 {price} 低于 {v}"
    if t == "above" and price > v: return f"价格 {price} 高于 {v}"
    if t == "any" and prev is not None and price != prev: return f"价格变动 {prev} -> {price}"
    if prev:
        ch = (price - prev) / prev * 100
        if t == "drop_pct" and -ch >= v: return f"降价 {-ch:.1f}%（{prev} -> {price}）"
        if t == "rise_pct" and ch >= v: return f"涨价 {ch:.1f}%（{prev} -> {price}）"
    if t == "map":
        m = _map_of(p)
        if m and price < m: return f"破价：{price} 低于最低限价 {m}（卖家 {p.get('seller') or p['platform']}）"
    return None
def on_event(event, pid=None, price=None, prev=None, in_stock=None):
    p = db.q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not p: return
    if event == "price":   # Webhook 事件：价格变动推送给订阅了 price_change 的集成方
        for ch in db.q("SELECT * FROM channels WHERE enabled=1 AND type='webhook' AND config LIKE '%price_change%'"):
            send(ch, "价格更新", f"{p['title']}：{prev} -> {price}", {"event": "price_change", "product_id": pid, "title": p["title"], "platform": p["platform"], "price": price, "prev": prev, "currency": p["currency"]}, event="price_change")
    for r in db.q("SELECT * FROM rules WHERE enabled=1 AND (product_id=? OR (product_id IS NULL AND (group_key IS NULL OR group_key=?) AND (category IS NULL OR category='' OR category=?)))", (pid, p["group_key"], p["category"] or "")):
        msg = _check(r, p, event, price, prev, in_stock)
        if not msg: continue
        if r["last_fired"] and r["product_id"] and (datetime.datetime.utcnow() - datetime.datetime.strptime(r["last_fired"], "%Y-%m-%d %H:%M:%S")).total_seconds() < r["cooldown_min"] * 60: continue
        db.ex("UPDATE rules SET last_fired=?, fired_count=fired_count+1 WHERE id=?", (_now(), r["id"]))
        db.ex("INSERT INTO alerts(product_id,msg) VALUES(?,?)", (pid, f"[{r['name']}] {msg}"))
        for cid in json.loads(r["channel_ids"] or "[]"):
            ch = db.q("SELECT * FROM channels WHERE id=? AND enabled=1", (cid,), one=True)
            if ch: send(ch, r["name"], f"{p['title']}（{p['platform']}）\n{msg}", {"event": "alert", "rule": r["name"], "product_id": pid, "title": p["title"], "price": price, "message": msg}, rule_id=r["id"])
HOOKS.append(on_event)

# ================= 智能同款匹配 =================
MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b|\b[A-Z]{1,4}\d{2,}[A-Z0-9]*\b")
def _model(p): m = p.get("model") or (MODEL_RE.search(p.get("title") or "") or [None])[0]; return (m or "").upper().replace("-", "") or None
@app.get("/api/match/suggest")
def match_suggest(threshold: float = 0.75, limit: int = 50):
    """同款候选：条码(GTIN)相同=1.0；否则 0.75×标题语义相似 + 0.15×品牌一致 + 0.10×图片相似 + 0.15×型号一致（封顶 0.99）。只列出跨平台、尚未归为同款的组合。"""
    ps = [p for p in products() if not p.get("parent_id")]
    if len(ps) < 2: return []
    raw = {r["id"]: r for r in db.q("SELECT id,gtin,model,phash FROM products")}
    docs = [search.doc(p) for p in ps]
    if search._ST: E = search._emb(docs); S = E @ E.T
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer
        X = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit_transform(docs); S = (X @ X.T).toarray()
    out = []
    import imagehash
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            a, b = ps[i], ps[j]
            if a["group_key"] == b["group_key"] or a["platform"] == b["platform"]: continue
            ra, rb = raw[a["id"]], raw[b["id"]]; why = []
            if ra["gtin"] and ra["gtin"] == rb["gtin"]: sc = 1.0; why.append(f"条码一致 {ra['gtin']}")
            else:
                ma, mb = _model({**a, **ra}), _model({**b, **rb}); mm = 1.0 if ma and ma == mb else 0.0
                br = 1.0 if a.get("brand") and (a.get("brand") or "").lower() == (b.get("brand") or "").lower() else 0.0
                im = 0.0
                if ra["phash"] and rb["phash"]: im = max(0.0, 1 - (imagehash.hex_to_hash(ra["phash"]) - imagehash.hex_to_hash(rb["phash"])) / 32)
                sc = 0.75 * float(S[i, j]) + 0.15 * br + 0.10 * im + 0.15 * mm
                why.append(f"标题相似 {float(S[i, j]):.0%}")
                if mm: why.append(f"型号一致 {ma}")
                if br: why.append("品牌一致")
                if im > 0.5: why.append(f"图片相似 {im:.0%}")
                if mm and float(S[i, j]) > 0.6: sc = max(sc, 0.9)
                if br and float(S[i, j]) > 0.8: sc = max(sc, 0.85)
                sc = min(sc, 0.99)
            if sc >= threshold:
                out.append({"a": {k: a[k] for k in ("id", "title", "platform", "last_price", "currency")}, "b": {k: b[k] for k in ("id", "title", "platform", "last_price", "currency")}, "score": round(sc, 3), "why": why})
    return sorted(out, key=lambda x: -x["score"])[:limit]
@app.post("/api/match/merge")
def match_merge(body: dict = Body(...)):
    ids = [int(i) for i in body.get("ids") or []]
    if len(ids) < 2: raise HTTPException(400, "至少选择 2 个商品")
    prev = db.q(f"SELECT id, group_key FROM products WHERE id IN ({','.join('?' * len(ids))})", ids)
    gk = next(r["group_key"] for r in prev if r["id"] == ids[0])
    db.ex(f"UPDATE products SET group_key=? WHERE id IN ({','.join('?' * len(ids))})", [gk] + ids)
    def restore():
        for r in prev: db.ex("UPDATE products SET group_key=? WHERE id=?", (r["group_key"], r["id"]))
    return {"ok": True, "group_key": gk, "undo": _undo_token(f"合并 {len(ids)} 个同款", restore), "message": f"已把 {len(ids)} 个商品标记为同款"}

# ================= 促销日历、季节性、买入建议 =================
def _nth_weekday(y, m, wd, n):
    d = datetime.date(y, m, 1); d += datetime.timedelta((wd - d.weekday()) % 7); return d + datetime.timedelta(weeks=n - 1)
def promo_events(year):
    bf = _nth_weekday(year, 11, 4, 4)
    return [("年货节", datetime.date(year, 1, 10), "中国", 10), ("女王节 3.8", datetime.date(year, 3, 8), "中国", 5), ("618 年中大促", datetime.date(year, 6, 18), "中国", 18),
            ("Prime Day 会员日", _nth_weekday(year, 7, 1, 2), "全球（亚马逊）", 2), ("99 大促", datetime.date(year, 9, 9), "中国/东南亚", 3), ("双 11", datetime.date(year, 11, 11), "中国/东南亚", 20),
            ("黑色星期五", bf, "欧美", 4), ("网络星期一", bf + datetime.timedelta(days=3), "欧美", 1), ("双 12", datetime.date(year, 12, 12), "中国/东南亚", 3), ("圣诞季", datetime.date(year, 12, 25), "欧美", 10)]
@app.get("/api/promo/calendar")
def promo_calendar(days: int = 365):
    today = datetime.date.today(); out = []
    for y in (today.year, today.year + 1):
        for name, d, region, span in promo_events(y):
            if -span <= (d - today).days <= days:
                out.append({"name": name, "date": d.isoformat(), "region": region, "days_left": (d - today).days, "warmup_days": span, "in_progress": (d - today).days <= 0})
    return sorted(out, key=lambda x: x["date"])
def seasonality(hist):
    if len(hist) < 14: return None
    by = {}
    for h in hist:
        try: wd = datetime.datetime.strptime(h["ts"][:10], "%Y-%m-%d").weekday()
        except Exception: continue
        by.setdefault(wd, []).append(h["price"])
    avg = float(np.mean([h["price"] for h in hist])); names = "一二三四五六日"
    dev = {f"周{names[k]}": round((float(np.mean(v)) / avg - 1) * 100, 2) for k, v in sorted(by.items())}
    cheapest = min(dev, key=dev.get) if dev else None
    return {"weekday_dev_pct": dev, "cheapest_day": cheapest, "note": f"历史上{cheapest}平均最便宜（{dev[cheapest]:+.1f}%）" if cheapest else ""}
@app.get("/api/products/{pid}/advice")
def buy_advice(pid: int):
    """买入时机建议：当前价在历史区间中的位置 + 走势 + 近期大促。"""
    p = product(pid); hist = db.q("SELECT price, ts FROM prices WHERE product_id=? ORDER BY ts, id", (pid,))
    if not hist: raise HTTPException(400, "还没有价格记录")
    ps = np.array([h["price"] for h in hist], float); cur = ps[-1]; lo, hi = ps.min(), ps.max()
    pct = float((ps < cur).mean()); f = analytics.forecast(hist, 7); promos = [e for e in promo_calendar(45) if e["days_left"] >= -e["warmup_days"]]
    reasons, score = [], 0
    if len(ps) >= 3:
        reasons.append(f"现价 {cur:g}，历史最低 {lo:g}、最高 {hi:g}，比 {pct:.0%} 的历史价格都高" if pct > 0 else f"现价 {cur:g} 是历史最低价")
        score += 2 if cur <= lo * 1.02 else (1 if pct <= 0.3 else (-1 if pct >= 0.7 else 0))
    else: reasons.append("历史记录少于 3 条，判断依据有限")
    if f.get("trend") == "下跌": score -= 1; reasons.append("近期走势在下跌，可能还会降")
    elif f.get("trend") == "上涨": score += 1; reasons.append("近期走势在上涨")
    if promos and not promos[0]["in_progress"] and promos[0]["days_left"] <= 30: score -= 1; reasons.append(f"{promos[0]['days_left']} 天后是「{promos[0]['name']}」，通常有促销")
    elif promos and promos[0]["in_progress"]: score += 1; reasons.append(f"「{promos[0]['name']}」正在进行")
    if p.get("target_price"): reasons.append(f"你的目标价 {p['target_price']:g}，" + ("已达到" if cur <= p["target_price"] else f"还差 {cur - p['target_price']:.2f}"))
    verdict = "现在买" if score >= 2 else ("可以买" if score == 1 else ("再等等" if score <= -1 else "观望"))
    season = seasonality(hist)
    if season: reasons.append(season["note"])
    conf = CF.pack(min(0.9, 0.3 + 0.05 * len(ps)), [f"基于 {len(ps)} 条历史价", "结合走势与促销日历"])
    return {"verdict": verdict, "score": score, "reasons": reasons, "percentile": round(pct, 3), "min": lo, "max": hi, "current": cur, "promos": promos[:3], "seasonality": season, "conf": conf}

# ================= MAP 控价、取证、卖家画像 =================
@app.get("/api/map/prices")
def map_prices(): return db.q("SELECT * FROM map_prices ORDER BY group_key")
@app.post("/api/map/prices")
def set_map(body: dict = Body(...)):
    if body.get("price") in (None, ""): db.ex("DELETE FROM map_prices WHERE group_key=?", (body["group_key"],))
    else: db.ex("INSERT OR REPLACE INTO map_prices(group_key,price,note) VALUES(?,?,?)", (body["group_key"], float(body["price"]), body.get("note", "")))
    return map_prices()
@app.get("/api/map/violations")
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
@app.post("/api/map/evidence/{pid}")
def evidence_task(pid: int): return submit("evidence", f"取证截图 #{pid}", lambda prog: capture_evidence(pid, prog))
@app.get("/api/map/evidence")
def evidence_list(): return db.q("SELECT e.*, p.title, p.platform, p.seller FROM evidence e LEFT JOIN products p ON p.id=e.product_id ORDER BY e.id DESC")
@app.get("/api/map/evidence/{eid}/image")
def evidence_image(eid: int):
    e = db.q("SELECT path FROM evidence WHERE id=?", (eid,), one=True)
    if not e or not os.path.exists(e["path"]): raise HTTPException(404, "截图不存在")
    return FileResponse(e["path"], media_type="image/png")
@app.get("/api/sellers")
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

# ================= 定价规则引擎与利润模拟 =================
STRATEGIES = {"beat_lowest": "比竞品最低价再低 X%", "match_lowest": "跟竞品最低价持平", "median": "对齐市场中位数", "cost_plus": "成本加成 X%", "premium": "比竞品均价高 X%"}
@app.get("/api/pricing/rules")
def pricing_rules(): return {"strategies": STRATEGIES, "items": db.q("SELECT * FROM pricing_rules ORDER BY id")}
@app.post("/api/pricing/rules")
def add_pricing_rule(body: dict = Body(...)):
    if body.get("strategy") not in STRATEGIES: raise HTTPException(400, "未知的定价策略")
    rid = db.ex("INSERT INTO pricing_rules(name,strategy,param,min_margin,floor,ceiling,respect_map,round_to,category) VALUES(?,?,?,?,?,?,?,?,?)",
                (body.get("name") or STRATEGIES[body["strategy"]], body["strategy"], float(body.get("param") or 0), float(body.get("min_margin", 0.1)), body.get("floor"), body.get("ceiling"),
                 int(body.get("respect_map", 1)), body.get("round_to", ""), body.get("category")))
    return db.q("SELECT * FROM pricing_rules WHERE id=?", (rid,), one=True)
@app.delete("/api/pricing/rules/{rid}")
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
@app.post("/api/pricing/simulate")
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
@app.post("/api/pricing/push")
def pricing_push(body: dict = Body(...)):
    """执行：把调价建议推送给订阅了 price_suggestion 事件的 Webhook（如自家 ERP），不直接改平台价格。"""
    r = simulate(body); n = 0
    chs = db.q("SELECT * FROM channels WHERE enabled=1 AND type='webhook' AND config LIKE '%price_suggestion%'")
    if not chs: raise HTTPException(400, "还没有订阅「调价建议」事件的 Webhook 渠道：在「告警与通知」新建 Webhook 渠道并勾选该事件")
    for ch in chs:
        ok, _ = send(ch, "调价建议", f"{len(r['rows'])} 个商品的调价建议", {"event": "price_suggestion", "items": [{k: x[k] for k in ("id", "title", "current", "suggested")} for x in r["rows"]]}, event="price_suggestion"); n += ok
    return {"ok": n > 0, "channels": n, "items": len(r["rows"])}

# ================= 报表与定时报告 =================
def report_data():
    ps = products(); groups = {}
    for p in ps:
        if p.get("last_price") is not None: groups.setdefault(p["group_key"], []).append(p)
    rows = []
    for g, items in groups.items():
        pr = [x["last_price"] for x in items]
        rows.append({"同款": items[0]["title"], "平台数": len({x["platform"] for x in items}), "最低价": min(pr), "最高价": max(pr), "价差%": round((max(pr) / min(pr) - 1) * 100, 1) if min(pr) else 0,
                     "最低价平台": min(items, key=lambda x: x["last_price"])["platform"]})
    rows.sort(key=lambda r: -r["价差%"])
    return {"generated_at": _now(), "products": len(ps), "groups": len(groups), "alerts_7d": db.q("SELECT COUNT(*) n FROM alerts WHERE ts >= datetime('now','-7 day')", one=True)["n"],
            "violations": map_violations(), "out_of_stock": [{"id": p["id"], "title": p["title"], "platform": p["platform"]} for p in ps if p.get("in_stock") == 0], "spread": rows}
def report_text(d):
    t = [f"商品 {d['products']} 个，同款组 {d['groups']} 个，近 7 天告警 {d['alerts_7d']} 条，破价 {len(d['violations'])} 个，缺货 {len(d['out_of_stock'])} 个。", "价差最大的同款："]
    t += [f"- {r['同款'][:30]}：{r['最低价']} ~ {r['最高价']}（{r['价差%']}%，最低在 {r['最低价平台']}）" for r in d["spread"][:5]]
    return "\n".join(t)
@app.get("/api/reports/summary")
def report(format: str = "json"):
    d = report_data()
    if format == "csv":
        b = io.StringIO(); w = csv.DictWriter(b, fieldnames=["同款", "平台数", "最低价", "最高价", "价差%", "最低价平台"]); w.writeheader(); w.writerows(d["spread"])
        return Response("\ufeff" + b.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=pricehub-report.csv"})
    if format == "html":
        tr = "".join(f"<tr><td>{r['同款']}</td><td>{r['平台数']}</td><td>{r['最低价']}</td><td>{r['最高价']}</td><td>{r['价差%']}%</td><td>{r['最低价平台']}</td></tr>" for r in d["spread"])
        vi = "".join(f"<tr><td>{v['title']}</td><td>{v['seller']}</td><td>{v['price']}</td><td>{v['map']}</td><td>-{v['gap_pct']}%</td></tr>" for v in d["violations"]) or "<tr><td colspan=5>无</td></tr>"
        return HTMLResponse(f"<html><head><meta charset=utf-8><title>PriceHub 报告</title><style>body{{font-family:sans-serif;padding:20px}}td,th{{border:1px solid #ccc;padding:4px 8px}}table{{border-collapse:collapse;margin-bottom:20px}}</style></head><body>"
                            f"<h2>PriceHub 价格报告</h2><p>生成时间(UTC) {d['generated_at']}</p><p>{report_text(d).splitlines()[0]}</p><h3>同款价差</h3><table><tr><th>同款</th><th>平台数</th><th>最低价</th><th>最高价</th><th>价差</th><th>最低价平台</th></tr>{tr}</table>"
                            f"<h3>破价（低于最低限价）</h3><table><tr><th>商品</th><th>卖家</th><th>现价</th><th>限价</th><th>低于</th></tr>{vi}</table></body></html>")
    return d
def send_report():
    cfg = get_setting("report_schedule", {}) or {}; d = report_data(); n = 0
    for cid in cfg.get("channel_ids") or []:
        ch = db.q("SELECT * FROM channels WHERE id=? AND enabled=1", (cid,), one=True)
        if ch: n += send(ch, "每日价格报告", report_text(d), {"event": "report", "summary": {k: d[k] for k in ("products", "groups", "alerts_7d")}}, event="report")[0]
    return n
@app.post("/api/reports/send")
def report_send(): return {"sent": send_report()}
@app.get("/api/reports/schedule")
def get_schedule(): return get_setting("report_schedule", {"enabled": False, "hour": 9, "channel_ids": []})
@app.post("/api/reports/schedule")
def set_schedule(body: dict = Body(...)):
    cfg = {"enabled": bool(body.get("enabled")), "hour": int(body.get("hour", 9)), "channel_ids": body.get("channel_ids") or []}; set_setting("report_schedule", cfg); _sched_report(); return cfg
def _sched_report():
    cfg = get_setting("report_schedule", {}) or {}
    try: sched.remove_job("report")
    except Exception: pass
    if cfg.get("enabled"): sched.add_job(send_report, "cron", hour=(cfg.get("hour", 9) - 8) % 24, id="report")   # 用户时间 UTC+8 → 服务器 UTC
_sched_report()

# ================= 采集设置（渲染、代理池） =================
@app.get("/api/collect/settings")
def collect_settings(): return {"render_fallback": get_setting("render_fallback", True), "proxies": get_setting("proxies", []), "render": render.status()}
@app.post("/api/collect/settings")
def set_collect(body: dict = Body(...)):
    if "render_fallback" in body: set_setting("render_fallback", bool(body["render_fallback"]))
    if "proxies" in body:
        ps = [p.strip() for p in body["proxies"] if p and p.strip()]
        bad = [p for p in ps if not re.match(r"^(https?|socks5)://", p)]
        if bad: raise HTTPException(400, f"代理格式不对：{bad[0]}（应为 http://host:port 或 socks5://host:port）")
        set_setting("proxies", ps)
    return collect_settings()
@app.post("/api/render/test")
def render_test(body: dict = Body(...)):
    from app import collect
    d, st, why, code = collect.fetch_parse(body["url"], force_render=bool(body.get("force", True)))
    return {"ok": bool(d and d.get("price")), "status": st, "detail": why, "http": code, "item": d}

# ================= API 令牌、角色、审计 =================
ROLES = {"admin": "管理员（全部权限）", "editor": "编辑（可增改，不能管理令牌/备份）", "viewer": "只读"}
ADMIN_PATHS = ("/api/tokens", "/api/backup", "/api/restore", "/api/audit")
def _hash(t): return hashlib.sha256(t.encode()).hexdigest()
@app.get("/api/tokens")
def tokens(): return {"roles": ROLES, "auth_required": bool(get_setting("auth_required", False)), "items": db.q("SELECT id,name,role,created FROM tokens ORDER BY id")}
@app.post("/api/tokens")
def add_token(body: dict = Body(...)):
    if body.get("role") not in ROLES: raise HTTPException(400, "角色可选：admin / editor / viewer")
    tok = "ph_" + secrets.token_urlsafe(24); db.ex("INSERT INTO tokens(name,token_hash,role) VALUES(?,?,?)", (body.get("name") or body["role"], _hash(tok), body["role"]))
    return {"token": tok, "role": body["role"], "message": "令牌只显示这一次，请妥善保存"}
@app.delete("/api/tokens/{tid}")
def del_token(tid: int): db.ex("DELETE FROM tokens WHERE id=?", (tid,)); return {"ok": True}
@app.post("/api/tokens/auth")
def set_auth(body: dict = Body(...)): set_setting("auth_required", bool(body.get("required"))); return tokens()
@app.get("/api/audit")
def audit(limit: int = 200): return db.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
@app.middleware("http")
async def auth_audit(request: Request, call_next):
    path, method = request.url.path, request.method; actor = "本机"
    key = request.headers.get("x-api-key"); ip = request.client.host if request.client else ""
    if path.startswith("/api/") and path != "/api/health":
        need = bool(get_setting("auth_required", False)) and (ip not in LOOPBACK or key is not None)
        if key is not None or need:
            t = db.q("SELECT * FROM tokens WHERE token_hash=?", (_hash(key or ""),), one=True)
            def deny(code, msg, who):
                db.ex("INSERT INTO audit(actor,method,path,status,ip) VALUES(?,?,?,?,?)", (who, method, path, code, ip)); return JSONResponse({"detail": msg}, status_code=code)
            if not t: return deny(401, "缺少或无效的 API 令牌（请求头 X-API-Key）", "无效令牌")
            actor = f"令牌:{t['name']}({t['role']})"
            if t["role"] == "viewer" and method != "GET": return deny(403, "只读令牌不能修改数据", actor)
            if t["role"] != "admin" and path.startswith(ADMIN_PATHS): return deny(403, "需要管理员令牌", actor)
    resp = await call_next(request)
    if path.startswith("/api/") and method in ("POST", "PUT", "DELETE") and not path.startswith("/api/tasks"):
        try: db.ex("INSERT INTO audit(actor,method,path,status,ip) VALUES(?,?,?,?,?)", (actor, method, path, resp.status_code, ip))
        except Exception: pass
    return resp

# ================= 备份与恢复 =================
@app.get("/api/backup")
def backup():
    out = db.export_sqlite(tempfile.mktemp(suffix=".db"))   # 任意后端都导出为 SQLite 备份文件
    return FileResponse(out, media_type="application/octet-stream", filename=f"pricehub-backup-{datetime.datetime.now():%Y%m%d-%H%M}.db")
@app.post("/api/restore")
async def restore(file: UploadFile = File(...)):
    tmp = tempfile.mktemp(suffix=".db"); open(tmp, "wb").write(await file.read())
    try:
        with sqlite3.connect(tmp) as c: names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception: raise HTTPException(400, "不是有效的 PriceHub 备份文件")
    if not {"products", "prices", "sources"} <= names: raise HTTPException(400, "备份文件缺少商品/价格/数据源表，不是 PriceHub 备份")
    if db.BACKEND == "postgresql":
        safety = db.export_sqlite(os.path.join(tempfile.gettempdir(), f"pricehub-before-restore-{int(time.time())}.db")); db.import_sqlite(tmp)
    else:
        safety = db.DB_PATH + f".before-restore-{int(time.time())}"; shutil.copy(db.DB_PATH, safety)
        with sqlite3.connect(tmp) as src, sqlite3.connect(db.DB_PATH) as dst: src.backup(dst)
    db.init(); db.VER[0] += 1
    return {"ok": True, "message": "已恢复。恢复前的数据已另存，可用于回退", "safety_copy": safety}

# ================= 书签一键收藏（无需安装插件） =================
@app.get("/bookmarklet", response_class=HTMLResponse)
def bookmarklet(request: Request):
    base = str(request.base_url).rstrip("/")
    js = f"javascript:(function(){{window.open('{base}/?monitor='+encodeURIComponent(location.href),'pricehub')}})()"
    return f"""<html><head><meta charset=utf-8><title>PriceHub 一键收藏</title><style>body{{font-family:sans-serif;padding:30px;line-height:1.8;background:#1e1e1e;color:#ddd}}a.bm{{display:inline-block;padding:8px 16px;background:#0e7ad1;color:#fff;border-radius:6px;text-decoration:none}}</style></head>
<body><h2>浏览器一键收藏商品</h2><p>把下面的按钮拖到浏览器书签栏。以后在任意电商商品页点一下这个书签，就会在 PriceHub 中打开并开始监控该商品。</p>
<p><a class="bm" href="{js}" data-bookmarklet>收藏到 PriceHub</a></p><p style="color:#999">支持 Chrome / Edge / Firefox / Safari，无需安装扩展。</p></body></html>"""

# 启动时把已有的 MAP（若有）与报表计划加载完成
os.makedirs(EVID_DIR, exist_ok=True)
