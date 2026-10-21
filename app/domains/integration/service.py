"""集成领域：报表与定时报告、书签工具（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
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
from app.domains.alerting.service import send
from app.domains.pricing.service import map_violations

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
def report_send(): return {"sent": send_report()}
def get_schedule(): return get_setting("report_schedule", {"enabled": False, "hour": 9, "channel_ids": []})
def set_schedule(body: dict = Body(...)):
    cfg = {"enabled": bool(body.get("enabled")), "hour": int(body.get("hour", 9)), "channel_ids": body.get("channel_ids") or []}; set_setting("report_schedule", cfg); _sched_report(); return cfg
def _sched_report():
    cfg = get_setting("report_schedule", {}) or {}
    try: sched.remove_job("report")
    except Exception: pass
    if cfg.get("enabled"): sched.add_job(send_report, "cron", hour=(cfg.get("hour", 9) - 8) % 24, id="report")   # 用户时间 UTC+8 → 服务器 UTC
def bookmarklet(request: Request):
    base = str(request.base_url).rstrip("/")
    js = f"javascript:(function(){{window.open('{base}/?monitor='+encodeURIComponent(location.href),'pricehub')}})()"
    return f"""<html><head><meta charset=utf-8><title>PriceHub 一键收藏</title><style>body{{font-family:sans-serif;padding:30px;line-height:1.8;background:#1e1e1e;color:#ddd}}a.bm{{display:inline-block;padding:8px 16px;background:#0e7ad1;color:#fff;border-radius:6px;text-decoration:none}}</style></head>
<body><h2>浏览器一键收藏商品</h2><p>把下面的按钮拖到浏览器书签栏。以后在任意电商商品页点一下这个书签，就会在 PriceHub 中打开并开始监控该商品。</p>
<p><a class="bm" href="{js}" data-bookmarklet>收藏到 PriceHub</a></p><p style="color:#999">支持 Chrome / Edge / Firefox / Safari，无需安装扩展。</p></body></html>"""
