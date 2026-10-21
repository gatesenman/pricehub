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

def collect_settings(): return {"render_fallback": get_setting("render_fallback", True), "proxies": get_setting("proxies", []), "render": render.status()}
def set_collect(body: dict = Body(...)):
    if "render_fallback" in body: set_setting("render_fallback", bool(body["render_fallback"]))
    if "proxies" in body:
        ps = [p.strip() for p in body["proxies"] if p and p.strip()]
        bad = [p for p in ps if not re.match(r"^(https?|socks5)://", p)]
        if bad: raise HTTPException(400, f"代理格式不对：{bad[0]}（应为 http://host:port 或 socks5://host:port）")
        set_setting("proxies", ps)
    return collect_settings()
def render_test(body: dict = Body(...)):
    from app import collect
    d, st, why, code = collect.fetch_parse(body["url"], force_render=bool(body.get("force", True)))
    return {"ok": bool(d and d.get("price")), "status": st, "detail": why, "http": code, "item": d}
