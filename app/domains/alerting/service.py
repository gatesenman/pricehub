"""告警领域：通知渠道与告警规则引擎（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401
from app.domains.common import _now

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
def channels(): return {"types": CHANNEL_TYPES, "items": db.q("SELECT * FROM channels ORDER BY id")}
def add_channel(body: dict = Body(...)):
    if body.get("type") not in CHANNEL_TYPES: raise HTTPException(400, f"渠道类型可选：{'、'.join(CHANNEL_TYPES.values())}")
    if body["type"] != "email" and not re.match(r"^https?://", body.get("url") or ""): raise HTTPException(400, "请填写以 http(s):// 开头的机器人/Webhook 地址")
    cid = db.ex("INSERT INTO channels(name,type,url,config,enabled) VALUES(?,?,?,?,?)", (body.get("name") or CHANNEL_TYPES[body["type"]], body["type"], body.get("url", ""), json.dumps(body.get("config") or {}, ensure_ascii=False), int(body.get("enabled", 1))))
    return db.q("SELECT * FROM channels WHERE id=?", (cid,), one=True)
def del_channel(cid: int): db.ex("DELETE FROM channels WHERE id=?", (cid,)); return {"ok": True}
def test_channel(cid: int):
    ch = db.q("SELECT * FROM channels WHERE id=?", (cid,), one=True)
    if not ch: raise HTTPException(404, "渠道不存在")
    ok, d = send(ch, "测试消息", "这是一条来自 PriceHub 的测试通知，收到说明渠道配置正确。", {"event": "test"}, event="test")
    return {"ok": ok, "detail": d}
def notifications(limit: int = 100): return db.q("SELECT n.*, c.name channel FROM notifications n LEFT JOIN channels c ON c.id=n.channel_id ORDER BY n.id DESC LIMIT ?", (limit,))
RULE_TYPES = {"target": "降到目标价（含）以下", "drop_pct": "单次降价超过 X%", "rise_pct": "单次涨价超过 X%", "below": "低于某个价格", "above": "高于某个价格",
              "stock": "库存变化（缺货/恢复有货）", "map": "低于最低限价（破价）", "any": "任何价格变动"}
def rules(): return {"types": RULE_TYPES, "items": db.q("SELECT r.*, p.title product FROM rules r LEFT JOIN products p ON p.id=r.product_id ORDER BY r.id DESC")}
def add_rule(body: dict = Body(...)):
    if body.get("type") not in RULE_TYPES: raise HTTPException(400, "未知的规则类型")
    if body["type"] not in ("stock", "map", "any") and body.get("value") in (None, ""): raise HTTPException(400, "请填写数值")
    rid = db.ex("INSERT INTO rules(name,type,value,product_id,group_key,category,channel_ids,cooldown_min,enabled) VALUES(?,?,?,?,?,?,?,?,1)",
                (body.get("name") or RULE_TYPES[body["type"]], body["type"], body.get("value"), body.get("product_id"), body.get("group_key"), body.get("category"),
                 json.dumps(body.get("channel_ids") or []), int(body.get("cooldown_min", 60))))
    return db.q("SELECT * FROM rules WHERE id=?", (rid,), one=True)
def edit_rule(rid: int, body: dict = Body(...)):
    for k in ("name", "value", "enabled", "cooldown_min"):
        if k in body: db.ex(f"UPDATE rules SET {k}=? WHERE id=?", (body[k], rid))
    if "channel_ids" in body: db.ex("UPDATE rules SET channel_ids=? WHERE id=?", (json.dumps(body["channel_ids"]), rid))
    return {"ok": True}
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
