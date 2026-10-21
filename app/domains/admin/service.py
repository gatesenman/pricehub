"""管理领域：API 令牌、角色、鉴权审计、备份与恢复（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401

ROLES = {"admin": "管理员（全部权限）", "editor": "编辑（可增改，不能管理令牌/备份）", "viewer": "只读"}
ADMIN_PATHS = ("/api/tokens", "/api/backup", "/api/restore", "/api/audit")
def _hash(t): return hashlib.sha256(t.encode()).hexdigest()
def tokens(): return {"roles": ROLES, "auth_required": bool(get_setting("auth_required", False)), "items": db.q("SELECT id,name,role,created FROM tokens ORDER BY id")}
def add_token(body: dict = Body(...)):
    if body.get("role") not in ROLES: raise HTTPException(400, "角色可选：admin / editor / viewer")
    tok = "ph_" + secrets.token_urlsafe(24); db.ex("INSERT INTO tokens(name,token_hash,role) VALUES(?,?,?)", (body.get("name") or body["role"], _hash(tok), body["role"]))
    return {"token": tok, "role": body["role"], "message": "令牌只显示这一次，请妥善保存"}
def del_token(tid: int): db.ex("DELETE FROM tokens WHERE id=?", (tid,)); return {"ok": True}
def set_auth(body: dict = Body(...)): set_setting("auth_required", bool(body.get("required"))); return tokens()
def audit(limit: int = 200): return db.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
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
def backup():
    out = db.export_sqlite(tempfile.mktemp(suffix=".db"))   # 任意后端都导出为 SQLite 备份文件
    return FileResponse(out, media_type="application/octet-stream", filename=f"pricehub-backup-{datetime.datetime.now():%Y%m%d-%H%M}.db")
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
