"""本地模拟 OIDC 提供方（仅用于测试）：发现文档 / 授权（按 login_hint 自动同意）/ 令牌（RS256 签名 id_token）/ JWKS。"""
import time, secrets, json, urllib.parse, jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse
from jwt.algorithms import RSAAlgorithm
ISS = "http://127.0.0.1:8971"; KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048); KID = "mock-1"; CODES = {}
app = FastAPI()
@app.get("/.well-known/openid-configuration")
def disc(): return {"issuer": ISS, "authorization_endpoint": ISS + "/authorize", "token_endpoint": ISS + "/token", "jwks_uri": ISS + "/jwks", "id_token_signing_alg_values_supported": ["RS256"]}
@app.get("/jwks")
def jwks(): k = json.loads(RSAAlgorithm.to_jwk(KEY.public_key())); k.update(kid=KID, use="sig", alg="RS256"); return {"keys": [k]}
@app.get("/authorize")
def authorize(client_id: str, redirect_uri: str, state: str, nonce: str = "", login_hint: str = "sso.user@example.com"):
    code = secrets.token_urlsafe(12); CODES[code] = {"client_id": client_id, "nonce": nonce, "email": login_hint}
    return RedirectResponse(redirect_uri + "?" + urllib.parse.urlencode({"code": code, "state": state}), status_code=302)
@app.post("/token")
def token(code: str = Form(...), client_id: str = Form(...), grant_type: str = Form("authorization_code"), redirect_uri: str = Form(""), client_secret: str = Form("")):
    c = CODES.pop(code, None)
    if not c or c["client_id"] != client_id or client_secret != "mock-secret": return {"error": "invalid_grant"}
    now = int(time.time()); e = c["email"]
    idt = jwt.encode({"iss": ISS, "aud": client_id, "sub": "sub-" + e, "email": e, "email_verified": True, "name": e.split("@")[0], "nonce": c["nonce"], "iat": now, "exp": now + 300},
                     KEY, algorithm="RS256", headers={"kid": KID})
    return {"access_token": "mock-at", "token_type": "Bearer", "id_token": idt, "expires_in": 300}
