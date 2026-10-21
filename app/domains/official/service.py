"""官方适配器：凭证存工作区 settings（加密），启用/测连/同步。"""
from __future__ import annotations
import json, os
from fastapi import HTTPException, Body
from app import db
from . import adapter, crypto
KEY = "official_adapters"
def _load():
    r = db.q("SELECT v FROM settings WHERE k=?", (KEY,), one=True)
    return json.loads(r["v"]) if r else {}
def _save(data):
    db.ex("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (KEY, json.dumps(data, ensure_ascii=False)))
def catalog():
    saved = _load(); out = []
    for m in adapter.list_adapters():
        s = saved.get(m["id"]) or {}
        creds = {k: crypto.mask(v) for k, v in (s.get("credentials") or {}).items()}
        out.append({**m, "enabled": bool(s.get("enabled")), "has_credentials": bool(s.get("credentials")),
                    "credentials_masked": creds, "config": s.get("config") or {}, "mock_base_url": s.get("mock_base_url") or "",
                    "last_test": s.get("last_test")})
    return {"items": out, "secret_source": "PRICEHUB_SECRET 环境变量" if os.environ.get("PRICEHUB_SECRET") else "本机 data/.secret 文件（自动生成）"}
def upsert(adapter_id: str, body: dict = Body(...)):
    if adapter_id not in adapter.REGISTRY: raise HTTPException(404, "未知适配器")
    data = _load(); cur = data.get(adapter_id) or {"credentials": {}, "config": {}}
    if "enabled" in body: cur["enabled"] = bool(body["enabled"])
    if "mock_base_url" in body: cur["mock_base_url"] = (body.get("mock_base_url") or "").strip()
    if isinstance(body.get("config"), dict): cur["config"] = {**(cur.get("config") or {}), **body["config"]}
    if isinstance(body.get("credentials"), dict):
        creds = dict(cur.get("credentials") or {})
        for k, v in body["credentials"].items():
            if v is None or v == "" or "•" in str(v): continue
            creds[k] = crypto.seal(str(v))
        cur["credentials"] = creds
    data[adapter_id] = cur; _save(data); return catalog()
def _open_creds(s): return {k: crypto.open_(v) for k, v in (s.get("credentials") or {}).items()}
def test(adapter_id: str, body: dict = Body(None)):
    body = body or {}; s = (_load().get(adapter_id) or {})
    mock = body["mock_base_url"] if "mock_base_url" in body else (s.get("mock_base_url") or "")
    try: ad = adapter.make(adapter_id, credentials=_open_creds(s), config=s.get("config") or {}, mock_base_url=mock)
    except KeyError: raise HTTPException(404, "未知适配器")
    r = ad.test_connection()
    data = _load(); cur = data.get(adapter_id) or {}; cur["last_test"] = {"ok": r["ok"], "error": r.get("error"), "count": r.get("count", 0)}; data[adapter_id] = cur; _save(data)
    return r
def sync(adapter_id: str, body: dict = Body(None)):
    from app.main import upsert as upsert_product
    body = body or {}; s = _load().get(adapter_id) or {}
    if not s.get("enabled") and not body.get("force"): raise HTTPException(400, "请先启用该适配器")
    mock = body["mock_base_url"] if "mock_base_url" in body else (s.get("mock_base_url") or "")
    ad = adapter.make(adapter_id, credentials=_open_creds(s), config={**(s.get("config") or {}), **(body.get("config") or {})}, mock_base_url=mock)
    items = ad.fetch_all(max_pages=int(body.get("max_pages") or 3))
    src = db.q("SELECT id FROM sources WHERE type=?", (f"official:{adapter_id}",), one=True)
    if not src:
        sid = db.ex("INSERT INTO sources(name,type,config,enabled) VALUES(?,?,?,1)", (ad.meta.name, f"official:{adapter_id}", json.dumps({"adapter": adapter_id})))
    else: sid = src["id"]
    ids = [upsert_product(sid, it) for it in items if it.get("title")]
    return {"ok": True, "fetched": len(items), "product_ids": ids, "audit": ad.audit_log()}
