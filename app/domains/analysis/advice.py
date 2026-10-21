"""分析领域：促销日历、季节性、买入建议（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401

def _nth_weekday(y, m, wd, n):
    d = datetime.date(y, m, 1); d += datetime.timedelta((wd - d.weekday()) % 7); return d + datetime.timedelta(weeks=n - 1)
def promo_events(year):
    bf = _nth_weekday(year, 11, 4, 4)
    return [("年货节", datetime.date(year, 1, 10), "中国", 10), ("女王节 3.8", datetime.date(year, 3, 8), "中国", 5), ("618 年中大促", datetime.date(year, 6, 18), "中国", 18),
            ("Prime Day 会员日", _nth_weekday(year, 7, 1, 2), "全球（亚马逊）", 2), ("99 大促", datetime.date(year, 9, 9), "中国/东南亚", 3), ("双 11", datetime.date(year, 11, 11), "中国/东南亚", 20),
            ("黑色星期五", bf, "欧美", 4), ("网络星期一", bf + datetime.timedelta(days=3), "欧美", 1), ("双 12", datetime.date(year, 12, 12), "中国/东南亚", 3), ("圣诞季", datetime.date(year, 12, 25), "欧美", 10)]
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
