"""采集领域：渲染与代理设置（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR  # noqa: F401

def collect_settings():
    st = render.status()
    return {"render_fallback": get_setting("render_fallback", True), "proxies": get_setting("proxies", []),
            "render_workers": get_setting("render_workers", st.get("workers", 1)), "render_queue": get_setting("render_queue", st.get("queue_max", 32)),
            "render": st, "pgvector": __import__("app.core.pgvector_store", fromlist=["status"]).status(),
            "extension_url": "/downloads/pricehub-clipper.zip"}
def set_collect(body: dict = Body(...)):
    if "render_fallback" in body: set_setting("render_fallback", bool(body["render_fallback"]))
    if "proxies" in body:
        ps = [p.strip() for p in body["proxies"] if p and p.strip()]
        bad = [p for p in ps if not re.match(r"^(https?|socks5)://", p)]
        if bad: raise HTTPException(400, f"代理格式不对：{bad[0]}（应为 http://host:port 或 socks5://host:port）")
        set_setting("proxies", ps)
    # 渲染池：写入环境变量式设置，供下次进程启动；当前进程仅在尚未启动 worker 时生效
    if body.get("render_workers") is not None or body.get("render_queue") is not None:
        w = body.get("render_workers"); q = body.get("render_queue")
        if w is not None: set_setting("render_workers", int(w)); os.environ["PRICEHUB_RENDER_WORKERS"] = str(int(w))
        if q is not None: set_setting("render_queue", int(q)); os.environ["PRICEHUB_RENDER_QUEUE"] = str(int(q))
        render.configure(workers=w)
    return collect_settings()
def render_test(body: dict = Body(...)):
    from app import collect
    d, st, why, code = collect.fetch_parse(body["url"], force_render=bool(body.get("force", True)))
    return {"ok": bool(d and d.get("price")), "status": st, "detail": why, "http": code, "item": d}
