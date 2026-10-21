"""工作区凭证加密：优先用 PRICEHUB_SECRET 环境变量；否则用本机 data/.secret 文件（自动生成）。
界面只回显遮掩后的值；解密仅在适配器运行时进行。"""
import os, base64, hashlib, secrets
from pathlib import Path
from cryptography.fernet import Fernet
def _key():
    raw = os.environ.get("PRICEHUB_SECRET")
    if not raw:
        p = Path(os.environ.get("PRICEHUB_SECRET_FILE", os.path.join(os.path.dirname(__import__("app").__file__), "..", "data", ".secret")))
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists(): p.write_text(secrets.token_urlsafe(32)); p.chmod(0o600)
        raw = p.read_text().strip()
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
def seal(plain: str) -> str:
    if plain is None or plain == "": return ""
    return "enc:" + Fernet(_key()).encrypt(plain.encode()).decode()
def open_(token: str) -> str:
    if not token: return ""
    if not str(token).startswith("enc:"): return token
    return Fernet(_key()).decrypt(token[4:].encode()).decode()
def mask(plain_or_enc: str) -> str:
    if not plain_or_enc: return ""
    try: p = open_(plain_or_enc) if str(plain_or_enc).startswith("enc:") else plain_or_enc
    except Exception: return "••••"
    if len(p) <= 4: return "•" * len(p)
    return p[:2] + "•" * (len(p) - 4) + p[-2:]
