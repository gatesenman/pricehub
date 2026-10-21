"""归一领域：汇率与到手价（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401
from app.domains.common import _latest

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
def get_fx(): return {"base": "CNY", "rates": fx(), "custom": get_setting("fx", {}) or {}, "note": "1 单位外币 = 多少人民币；默认值为内置参考汇率，可按需覆盖"}
def set_fx(body: dict = Body(...)):
    cur = get_setting("fx", {}) or {}
    for k, v in (body.get("rates") or {}).items():
        if v in (None, ""): cur.pop(k.upper(), None)
        else: cur[k.upper()] = float(v)
    set_setting("fx", cur); return get_fx()
EDITABLE = ("shipping", "tax_rate", "coupon", "map_price", "target_price", "cost", "gtin", "model", "category", "brand")
def set_costs(pid: int, body: dict = Body(...)):
    for k, v in body.items():
        if k in EDITABLE: db.ex(f"UPDATE products SET {k}=? WHERE id=?", (None if v in ("", None) else v, pid))
    if body.get("target_price") not in (None, ""):   # 设置目标价即自动创建一条目标价提醒规则
        if not db.q("SELECT id FROM rules WHERE type='target' AND product_id=?", (pid,), one=True):
            db.ex("INSERT INTO rules(name,type,value,product_id,channel_ids) VALUES(?,?,?,?,?)", (f"目标价提醒 #{pid}", "target", float(body["target_price"]), pid, json.dumps([c["id"] for c in db.q("SELECT id FROM channels WHERE enabled=1")])))
        else: db.ex("UPDATE rules SET value=?, enabled=1 WHERE type='target' AND product_id=?", (float(body["target_price"]), pid))
    return product_full(pid)
def product_full(pid: int):
    p = product(pid); p["landed"] = landed(p); p["variants"] = db.q("SELECT id,variant,title,in_stock,(SELECT price FROM prices WHERE product_id=products.id ORDER BY id DESC LIMIT 1) price FROM products WHERE parent_id=?", (pid,))
    p["map_effective"] = p.get("map_price") or (db.q("SELECT price FROM map_prices WHERE group_key=?", (p["group_key"],), one=True) or {}).get("price")
    p["rules"] = db.q("SELECT * FROM rules WHERE product_id=? OR (product_id IS NULL AND (group_key IS NULL OR group_key=?))", (pid, p["group_key"]))
    return p
def compare_landed(group_key: str):
    out = []
    for r in db.q("SELECT * FROM products WHERE group_key=?", (group_key,)):
        lp = _latest(r["id"])
        if lp: r.pop("clip", None); out.append({**{k: r[k] for k in ("id", "platform", "title", "seller", "currency", "in_stock")}, **landed(r, lp["price"])})
    return sorted(out, key=lambda x: (x["landed_cny"] is None, x["landed_cny"] or 0))
