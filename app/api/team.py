"""v7 团队协作接口：登录/注册/单点登录、工作区切换、成员与邀请、用量。以及决定“当前工作区”的身份中间件。"""
import json, hashlib
from fastapi import Body, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from app import db
from app.main import app
from app.core import tenancy
from app.core.events import bus
from app.domains.team import service as T, usage as U
COOKIE = "ph_session"
PUBLIC = ("/api/health", "/api/auth/", "/api/invites/preview")
ADMIN_ONLY = ("/api/tokens", "/api/backup", "/api/restore", "/api/audit", "/api/workspace/members", "/api/workspace/invites", "/api/workspace/settings")
def _tok(request): return request.cookies.get(COOKIE) or (request.headers.get("authorization", "")[7:] if request.headers.get("authorization", "").startswith("Session ") else None)
def ctx(request: Request):
    s = getattr(request.state, "ph", None)
    if not s: raise HTTPException(401, "请先登录")
    return s
@app.middleware("http")
async def identity(request: Request, call_next):
    """确定当前用户与工作区：会话 Cookie / API 令牌 → 工作区；团队模式下未登录的接口请求返回 401；单机模式默认工作区免登录。"""
    path = request.url.path; request.state.ph = None; ws = 1; role = None
    if path.startswith("/api/") or path == "/metrics":
        s = T.session(_tok(request))
        if s:
            role = T.role_in(s["user_id"], s["ws_id"]); ws = s["ws_id"]
            request.state.ph = {"user_id": s["user_id"], "email": s["email"], "name": s["name"], "ws": ws, "role": role, "token": _tok(request)}
        else:
            key = request.headers.get("x-api-key")
            if key:
                with tenancy.bypass(): t = db.q("SELECT workspace_id FROM tokens WHERE token_hash=?", (hashlib.sha256(key.encode()).hexdigest(),), one=True)
                if t: ws = t["workspace_id"]; role = "token"
        if path.startswith("/api/") and not path.startswith(PUBLIC):
            if T.team_mode() and role is None: return JSONResponse({"detail": "请先登录", "need_login": True}, status_code=401)
            if role == "viewer" and request.method != "GET": return JSONResponse({"detail": "只读成员不能修改数据"}, status_code=403)
            if role == "editor" and path.startswith(ADMIN_ONLY) and not (request.method == "GET" and path.startswith("/api/workspace/members")):
                return JSONResponse({"detail": "需要工作区管理员权限"}, status_code=403)
            try: U.check("api_calls", ws=ws)
            except U.QuotaExceeded as e: return JSONResponse({"detail": str(e)}, status_code=429)
            U.inc("api_calls", ws=ws)
    tenancy.set_current(ws)
    return await call_next(request)
def _set_cookie(resp, tok): resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=T.SESSION_DAYS * 86400); return resp
@app.get("/api/auth/me")
def me(request: Request):
    s = request.state.ph; tm = T.team_mode()
    base = {"team_mode": tm, "has_users": bool(T.G("SELECT id FROM users LIMIT 1", one=True)), "oidc": {"enabled": bool(T.oidc_config().get("issuer")), "name": T.oidc_config().get("name") or "单点登录"}}
    if not s: return base | {"logged_in": False, "need_login": tm, "workspace": {"id": 1, "name": "默认工作区"}, "role": "owner" if not tm else None, "workspaces": []}
    ws = T.G("SELECT id, name, plan FROM workspaces WHERE id=?", (s["ws"],), one=True)
    return base | {"logged_in": True, "need_login": False, "user": {"id": s["user_id"], "email": s["email"], "name": s["name"]}, "workspace": ws, "role": s["role"], "roles": T.ROLES, "workspaces": T.memberships(s["user_id"])}
@app.post("/api/auth/register")
def register(body: dict = Body(...)):
    if T.team_mode() and T.G("SELECT id FROM users LIMIT 1", one=True) and not body.get("invite"): raise HTTPException(403, "团队模式下需要邀请链接才能注册")
    uid = T.create_user(body.get("email"), body.get("name"), body.get("password") or "")
    if body.get("invite"): T.accept(body["invite"], uid)
    tok = T.new_session(uid); return _set_cookie(JSONResponse({"ok": True}), tok)
@app.post("/api/auth/login")
def login(body: dict = Body(...)): return _set_cookie(JSONResponse({"ok": True}), T.login(body.get("email"), body.get("password")))
@app.post("/api/auth/logout")
def logout(request: Request):
    T.end_session(_tok(request)); r = JSONResponse({"ok": True}); r.delete_cookie(COOKIE); return r
@app.post("/api/auth/switch")
def switch(request: Request, body: dict = Body(...)): s = ctx(request); T.switch(s["token"], int(body["ws_id"])); return {"ok": True}
@app.post("/api/auth/mode")
def set_mode(request: Request, body: dict = Body(...)):
    s = request.state.ph
    if T.team_mode() and not (s and s["role"] == "owner" and s["ws"] == 1): raise HTTPException(403, "只有默认工作区的所有者可以修改团队模式")
    if not T.team_mode() and request.client and request.client.host not in ("127.0.0.1", "::1", "testclient") and not (s and s["role"] == "owner"): raise HTTPException(403, "请在本机开启团队模式")
    T.set_team_mode(bool(body.get("enabled"))); return {"team_mode": T.team_mode()}
@app.get("/api/auth/oidc/config")
def oidc_cfg(request: Request):
    c = T.oidc_config(); s = request.state.ph
    return {"enabled": bool(c.get("issuer")), "name": c.get("name") or "单点登录", **({"issuer": c.get("issuer"), "client_id": c.get("client_id")} if s and s["role"] in ("owner", "admin") or not T.team_mode() else {})}
@app.post("/api/auth/oidc/config")
def oidc_set(request: Request, body: dict = Body(...)):
    s = request.state.ph
    if T.team_mode() and not (s and s["role"] == "owner" and s["ws"] == 1): raise HTTPException(403, "只有默认工作区的所有者可以配置单点登录")
    T.set_oidc_config(body); return {"ok": True}
@app.get("/api/auth/oidc/login")
def oidc_login(request: Request, invite: str = "", login_hint: str = ""): return RedirectResponse(T.oidc_start(str(request.base_url), invite or None, login_hint or None), status_code=302)
@app.get("/api/auth/oidc/callback")
def oidc_cb(code: str = "", state: str = "", error: str = ""):
    if error: return RedirectResponse(f"/?login_error={error}", status_code=302)
    try: tok = T.oidc_finish(code, state)
    except HTTPException as e: return RedirectResponse("/?login_error=" + __import__("urllib.parse").parse.quote(e.detail), status_code=302)
    return _set_cookie(RedirectResponse("/", status_code=302), tok)
@app.get("/api/invites/preview")
def invite_preview(token: str):
    i = T.G("SELECT i.email, i.role, i.accepted_at, w.name ws FROM invites i JOIN workspaces w ON w.id=i.ws_id WHERE i.token_hash=?", (T._h(token),), one=True)
    if not i or i["accepted_at"]: raise HTTPException(404, "邀请链接无效或已使用")
    return {"email": i["email"], "role": T.ROLES[i["role"]], "workspace": i["ws"]}
@app.post("/api/invites/accept")
def invite_accept(request: Request, body: dict = Body(...)):
    s = ctx(request); ws = T.accept(body.get("token"), s["user_id"]); T.switch(s["token"], ws); return {"ok": True, "ws_id": ws}
# ---------------- 工作区 ----------------
@app.get("/api/workspaces")
def workspaces(request: Request):
    s = request.state.ph
    return T.memberships(s["user_id"]) if s else T.G("SELECT id, name, slug, plan, 'owner' role FROM workspaces WHERE id=1")
@app.post("/api/workspaces")
def ws_create(request: Request, body: dict = Body(...)):
    s = ctx(request); wid = T.create_workspace(body.get("name"), s["user_id"], body.get("plan") or "free")
    if body.get("switch", True): T.switch(s["token"], wid)
    return {"id": wid}
def _admin(request):
    s = ctx(request)
    if s["role"] not in ("owner", "admin"): raise HTTPException(403, "需要工作区管理员权限")
    return s
@app.get("/api/workspace/members")
def ws_members(request: Request): s = ctx(request); return {"roles": T.ROLES, "items": T.members(s["ws"]), "invites": T.invites(s["ws"]) if s["role"] in ("owner", "admin") else []}
@app.put("/api/workspace/members/{uid}")
def ws_role(uid: int, request: Request, body: dict = Body(...)): s = _admin(request); T.set_role(s["ws"], uid, body.get("role"), s["role"]); return {"ok": True}
@app.delete("/api/workspace/members/{uid}")
def ws_remove(uid: int, request: Request): s = _admin(request); T.remove_member(s["ws"], uid); return {"ok": True}
@app.post("/api/workspace/invites")
def ws_invite(request: Request, body: dict = Body(...)): s = _admin(request); return T.invite(s["ws"], body.get("email"), body.get("role") or "editor", s["user_id"], s["role"])
@app.delete("/api/workspace/invites/{iid}")
def ws_invite_del(iid: int, request: Request): s = _admin(request); T.revoke_invite(s["ws"], iid); return {"ok": True}
@app.put("/api/workspace/settings")
def ws_settings(request: Request, body: dict = Body(...)):
    s = _admin(request)
    if body.get("name"): T.X("UPDATE workspaces SET name=? WHERE id=?", (body["name"].strip(), s["ws"]))
    if body.get("plan"):
        if s["role"] != "owner": raise HTTPException(403, "只有所有者可以修改套餐")
        if body["plan"] not in U.PLANS: raise HTTPException(400, "无效套餐")
        T.X("UPDATE workspaces SET plan=? WHERE id=?", (body["plan"], s["ws"]))
    if isinstance(body.get("quota"), dict):
        if s["role"] != "owner": raise HTTPException(403, "只有所有者可以调整配额")
        T.X("UPDATE workspaces SET quota=? WHERE id=?", (json.dumps({k: (int(v) if v not in (None, "") else None) for k, v in body["quota"].items()}), s["ws"]))
    return {"ok": True}
@app.get("/api/usage")
def usage(): return U.report()
# 采集计量：订阅事件总线（采集在任务线程里执行，工作区已由调度器恢复）
bus.subscribe("collect.succeeded", lambda t, **k: U.inc("collects"), "team.usage")
bus.subscribe("collect.failed", lambda t, **k: U.inc("collects"), "team.usage")
