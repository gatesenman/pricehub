"""匹配领域：同款建议与合并（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.domains.common import *  # noqa: F401,F403
from app.domains.common import _now, _latest, EVID_DIR, _undo_token  # noqa: F401

MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b|\b[A-Z]{1,4}\d{2,}[A-Z0-9]*\b")
def _model(p): m = p.get("model") or (MODEL_RE.search(p.get("title") or "") or [None])[0]; return (m or "").upper().replace("-", "") or None
def match_suggest(threshold: float = 0.75, limit: int = 50):
    """同款候选：条码(GTIN)相同=1.0；否则 0.75×标题语义相似 + 0.15×品牌一致 + 0.10×图片相似 + 0.15×型号一致（封顶 0.99）。只列出跨平台、尚未归为同款的组合。"""
    ps = [p for p in products() if not p.get("parent_id")]
    if len(ps) < 2: return []
    raw = {r["id"]: r for r in db.q("SELECT id,gtin,model,phash FROM products")}
    docs = [search.doc(p) for p in ps]
    if search._ST: E = search._emb(docs); S = E @ E.T
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer
        X = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit_transform(docs); S = (X @ X.T).toarray()
    out = []
    import imagehash
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            a, b = ps[i], ps[j]
            if a["group_key"] == b["group_key"] or a["platform"] == b["platform"]: continue
            ra, rb = raw[a["id"]], raw[b["id"]]; why = []
            if ra["gtin"] and ra["gtin"] == rb["gtin"]: sc = 1.0; why.append(f"条码一致 {ra['gtin']}")
            else:
                ma, mb = _model({**a, **ra}), _model({**b, **rb}); mm = 1.0 if ma and ma == mb else 0.0
                br = 1.0 if a.get("brand") and (a.get("brand") or "").lower() == (b.get("brand") or "").lower() else 0.0
                im = 0.0
                if ra["phash"] and rb["phash"]: im = max(0.0, 1 - (imagehash.hex_to_hash(ra["phash"]) - imagehash.hex_to_hash(rb["phash"])) / 32)
                sc = 0.75 * float(S[i, j]) + 0.15 * br + 0.10 * im + 0.15 * mm
                why.append(f"标题相似 {float(S[i, j]):.0%}")
                if mm: why.append(f"型号一致 {ma}")
                if br: why.append("品牌一致")
                if im > 0.5: why.append(f"图片相似 {im:.0%}")
                if mm and float(S[i, j]) > 0.6: sc = max(sc, 0.9)
                if br and float(S[i, j]) > 0.8: sc = max(sc, 0.85)
                sc = min(sc, 0.99)
            if sc >= threshold:
                out.append({"a": {k: a[k] for k in ("id", "title", "platform", "last_price", "currency")}, "b": {k: b[k] for k in ("id", "title", "platform", "last_price", "currency")}, "score": round(sc, 3), "why": why})
    return sorted(out, key=lambda x: -x["score"])[:limit]
def match_merge(body: dict = Body(...)):
    ids = [int(i) for i in body.get("ids") or []]
    if len(ids) < 2: raise HTTPException(400, "至少选择 2 个商品")
    prev = db.q(f"SELECT id, group_key FROM products WHERE id IN ({','.join('?' * len(ids))})", ids)
    gk = next(r["group_key"] for r in prev if r["id"] == ids[0])
    db.ex(f"UPDATE products SET group_key=? WHERE id IN ({','.join('?' * len(ids))})", [gk] + ids)
    def restore():
        for r in prev: db.ex("UPDATE products SET group_key=? WHERE id=?", (r["group_key"], r["id"]))
    return {"ok": True, "group_key": gk, "undo": _undo_token(f"合并 {len(ids)} 个同款", restore), "message": f"已把 {len(ids)} 个商品标记为同款"}
