"""账号、会话、工作区、成员、邀请、OIDC 单点登录。邀请只生成链接，不发送任何邮件或消息。"""
import hashlib, secrets, time, json, datetime, urllib.parse, threading, re
import httpx
from fastapi import HTTPException
from app import db
from app.core import tenancy
ROLES = {"owner": "所有者", "admin": "管理员", "editor": "编辑", "viewer": "只读"}
RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
SESSION_DAYS = 14
def _h(t): return hashlib.sha256(t.encode()).hexdigest()
def _now(): return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
def hash_password(pw):
    salt = secrets.token_hex(16); d = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex(); return f"scrypt${salt}${d}"
def verify_password(pw, stored):
    try: _, salt, d = stored.split("$"); return secrets.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex(), d)
    except Exception: return False
def G(sql, args=(), one=False):
    with tenancy.bypass(): return db.q(sql, args, one=one)
def X(sql, args=()):
    with tenancy.bypass(): return db.ex(sql, args)
# ---------------- 团队模式（全局开关，存于默认工作区设置） ----------------
def team_mode():
    r = G("SELECT v FROM settings WHERE workspace_id=1 AND k='team_mode'", one=True); return bool(r and json.loads(r["v"]))
def set_team_mode(on):
    if on and not G("SELECT id FROM users LIMIT 1", one=True): raise HTTPException(400, "请先创建管理员账号，再开启团队模式")
    X("INSERT OR REPLACE INTO settings(k,v,workspace_id) VALUES('team_mode',?,1)", (json.dumps(bool(on)),))
# ---------------- 用户与会话 ----------------
def _valid_email(e): return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e or ""))
def create_user(email, name="", password=None, issuer=None, sub=None):
    email = (email or "").strip().lower()
    if not _valid_email(email): raise HTTPException(400, "请填写正确的邮箱")
    if password is not None and len(password) < 8: raise HTTPException(400, "密码至少 8 位")
    if G("SELECT id FROM users WHERE email=?", (email,), one=True): raise HTTPException(409, "这个邮箱已经注册过了")
    uid = X("INSERT INTO users(email,name,password_hash,oidc_issuer,oidc_sub) VALUES(?,?,?,?,?)", (email, name or email.split("@")[0], hash_password(password) if password else None, issuer, sub))
    first = G("SELECT COUNT(*) n FROM users", one=True)["n"] == 1
    if first: add_member(1, uid, "owner")          # 第一个账号成为默认工作区的所有者（接管单机数据）
    return uid
def add_member(ws, uid, role):
    if role not in ROLES: raise HTTPException(400, "角色可选：" + "、".join(f"{k}（{v}）" for k, v in ROLES.items()))
    if G("SELECT id FROM memberships WHERE user_id=? AND ws_id=?", (uid, ws), one=True): X("UPDATE memberships SET role=? WHERE user_id=? AND ws_id=?", (role, uid, ws))
    else: X("INSERT INTO memberships(user_id,ws_id,role) VALUES(?,?,?)", (uid, ws, role))
def create_workspace(name, owner_uid, plan="free"):
    name = (name or "").strip()
    if not name: raise HTTPException(400, "请填写工作区名称")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "ws"; slug = f"{slug}-{secrets.token_hex(3)}"
    wid = X("INSERT INTO workspaces(name,slug,plan) VALUES(?,?,?)", (name, slug, plan)); add_member(wid, owner_uid, "owner"); return wid
def memberships(uid): return G("SELECT w.id, w.name, w.slug, w.plan, m.role FROM memberships m JOIN workspaces w ON w.id=m.ws_id WHERE m.user_id=? ORDER BY w.id", (uid,))
def role_in(uid, ws):
    r = G("SELECT role FROM memberships WHERE user_id=? AND ws_id=?", (uid, ws), one=True); return r and r["role"]
def new_session(uid, ws=None):
    ms = memberships(uid)
    if not ms:
        u = G("SELECT * FROM users WHERE id=?", (uid,), one=True); ws = create_workspace(f"{u['name']} 的工作区", uid); ms = memberships(uid)
    ws = ws if ws and any(m["id"] == ws for m in ms) else ms[0]["id"]
    tok = secrets.token_urlsafe(32); exp = (datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    X("INSERT INTO sessions(token_hash,user_id,ws_id,expires) VALUES(?,?,?,?)", (_h(tok), uid, ws, exp)); X("UPDATE users SET last_login=? WHERE id=?", (_now(), uid))
    return tok
def session(tok):
    if not tok: return None
    s = G("SELECT s.*, u.email, u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (_h(tok),), one=True)
    if not s or s["expires"] < _now(): return None
    return s
def end_session(tok): X("DELETE FROM sessions WHERE token_hash=?", (_h(tok or ""),))
def switch(tok, ws):
    s = session(tok)
    if not s or not role_in(s["user_id"], ws): raise HTTPException(403, "你不是这个工作区的成员")
    X("UPDATE sessions SET ws_id=? WHERE token_hash=?", (ws, _h(tok)))
def login(email, password):
    u = G("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),), one=True)
    if not u or not u["password_hash"] or not verify_password(password or "", u["password_hash"]): raise HTTPException(401, "邮箱或密码不对")
    return new_session(u["id"])
# ---------------- 成员与邀请 ----------------
def members(ws): return G("SELECT u.id, u.email, u.name, m.role, m.created, u.last_login FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.ws_id=? ORDER BY m.id", (ws,))
def set_role(ws, uid, role, actor_role):
    if role not in ROLES: raise HTTPException(400, "无效角色")
    if RANK[role] > RANK[actor_role]: raise HTTPException(403, "不能授予比自己更高的角色")
    cur = role_in(uid, ws)
    if not cur: raise HTTPException(404, "不是本工作区成员")
    if cur == "owner" and role != "owner" and G("SELECT COUNT(*) n FROM memberships WHERE ws_id=? AND role='owner'", (ws,), one=True)["n"] <= 1: raise HTTPException(400, "工作区至少要保留一位所有者")
    X("UPDATE memberships SET role=? WHERE ws_id=? AND user_id=?", (role, ws, uid))
def remove_member(ws, uid):
    if role_in(uid, ws) == "owner" and G("SELECT COUNT(*) n FROM memberships WHERE ws_id=? AND role='owner'", (ws,), one=True)["n"] <= 1: raise HTTPException(400, "不能移除唯一的所有者")
    X("DELETE FROM memberships WHERE ws_id=? AND user_id=?", (ws, uid)); X("DELETE FROM sessions WHERE user_id=? AND ws_id=?", (uid, ws))
def invite(ws, email, role, by_uid, actor_role):
    if role not in ROLES or role == "owner": raise HTTPException(400, "邀请角色可选：管理员、编辑、只读")
    if RANK[role] > RANK[actor_role]: raise HTTPException(403, "不能邀请比自己权限更高的角色")
    if not _valid_email(email): raise HTTPException(400, "请填写正确的邮箱")
    tok = secrets.token_urlsafe(24); iid = X("INSERT INTO invites(ws_id,email,role,token_hash,created_by) VALUES(?,?,?,?,?)", (ws, email.strip().lower(), role, _h(tok), by_uid))
    return {"id": iid, "token": tok, "invite_path": f"/?invite={tok}", "message": "邀请已生成。系统不会自动发邮件，请把链接发给对方"}
def invites(ws): return G("SELECT id, email, role, created, accepted_at FROM invites WHERE ws_id=? ORDER BY id DESC", (ws,))
def revoke_invite(ws, iid): X("DELETE FROM invites WHERE ws_id=? AND id=? AND accepted_at IS NULL", (ws, iid))
def accept(tok, uid):
    i = G("SELECT * FROM invites WHERE token_hash=?", (_h(tok or ""),), one=True)
    if not i or i["accepted_at"]: raise HTTPException(404, "邀请链接无效或已使用")
    u = G("SELECT email FROM users WHERE id=?", (uid,), one=True)
    if u["email"] != i["email"]: raise HTTPException(403, f"这个邀请是发给 {i['email']} 的，请用该邮箱登录后再接受")
    add_member(i["ws_id"], uid, i["role"]); X("UPDATE invites SET accepted_at=? WHERE id=?", (_now(), i["id"])); return i["ws_id"]
# ---------------- OIDC 单点登录 ----------------
_STATES = {}; _SL = threading.Lock(); _DISC = {}
def oidc_config():
    r = G("SELECT v FROM settings WHERE workspace_id=1 AND k='oidc'", one=True); return json.loads(r["v"]) if r else {}
def set_oidc_config(cfg):
    cfg = {k: (cfg.get(k) or "").strip() for k in ("issuer", "client_id", "client_secret", "name")}
    if not cfg["client_secret"]: cfg["client_secret"] = oidc_config().get("client_secret", "")   # 留空表示不修改
    if cfg["issuer"] and not re.match(r"^https?://", cfg["issuer"]): raise HTTPException(400, "发行方地址需以 http(s):// 开头")
    X("INSERT OR REPLACE INTO settings(k,v,workspace_id) VALUES('oidc',?,1)", (json.dumps(cfg),)); _DISC.clear()
def discovery(issuer):
    if issuer not in _DISC: _DISC[issuer] = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10).json()
    return _DISC[issuer]
def oidc_start(base_url, invite=None, login_hint=None):
    c = oidc_config()
    if not c.get("issuer") or not c.get("client_id"): raise HTTPException(400, "还没有配置单点登录（OIDC）")
    d = discovery(c["issuer"]); state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    with _SL:
        for k in [k for k, v in _STATES.items() if time.time() - v["ts"] > 600]: _STATES.pop(k)
        _STATES[state] = {"nonce": nonce, "ts": time.time(), "invite": invite, "redirect_uri": base_url.rstrip("/") + "/api/auth/oidc/callback"}
    q = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": _STATES[state]["redirect_uri"], "scope": "openid email profile", "state": state, "nonce": nonce}
    if login_hint: q["login_hint"] = login_hint
    return d["authorization_endpoint"] + "?" + urllib.parse.urlencode(q)
def oidc_finish(code, state):
    import jwt
    with _SL: st = _STATES.pop(state or "", None)
    if not st: raise HTTPException(400, "登录状态已过期，请重新点击单点登录")
    c = oidc_config(); d = discovery(c["issuer"])
    r = httpx.post(d["token_endpoint"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": st["redirect_uri"], "client_id": c["client_id"], "client_secret": c.get("client_secret", "")}, timeout=10)
    if r.status_code != 200: raise HTTPException(401, f"身份提供方拒绝了登录：HTTP {r.status_code}")
    idt = r.json().get("id_token")
    if not idt: raise HTTPException(401, "身份提供方没有返回身份令牌")
    key = jwt.PyJWKClient(d["jwks_uri"]).get_signing_key_from_jwt(idt).key
    claims = jwt.decode(idt, key, algorithms=["RS256", "ES256"], audience=c["client_id"], issuer=d.get("issuer", c["issuer"]))
    if claims.get("nonce") != st["nonce"]: raise HTTPException(401, "登录校验失败（nonce 不匹配）")
    email = (claims.get("email") or "").lower(); iss, sub = claims["iss"], claims["sub"]
    u = G("SELECT * FROM users WHERE oidc_issuer=? AND oidc_sub=?", (iss, sub), one=True) or (G("SELECT * FROM users WHERE email=?", (email,), one=True) if email and claims.get("email_verified", True) else None)
    if u:
        if not u["oidc_sub"]: X("UPDATE users SET oidc_issuer=?, oidc_sub=? WHERE id=?", (iss, sub, u["id"]))
        uid = u["id"]
    else: uid = create_user(email, claims.get("name") or "", None, iss, sub)
    if st.get("invite"):
        try: accept(st["invite"], uid)
        except HTTPException: pass
    return new_session(uid)
