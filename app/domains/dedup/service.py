"""v12 同款去重：候选检测（同平台不同商品页 + 跨平台同款）、打分与解释、复核队列、合并 / 撤销合并、自动合并阈值、新采集商品自动检测。

打分（0..1）：
- 条码 / ASIN 一致 → 0.99；条码不同 → 封顶 0.30（明确不是同一 SKU）
- 否则 0.45×标题语义相似（多语言向量，无模型时 TF-IDF） + 0.20×型号/系列一致 + 0.10×品牌一致 + 0.10×容量一致 + 0.05×颜色一致 + 0.10×图片相似（CLIP，无则 phash）
- 硬冲突封顶：品牌不同 ≤0.30，容量不同 ≤0.40，型号/系列不同 ≤0.50，颜色不同 ≤0.60
合并语义：保留主商品（canonical）；被合并商品行保留（merged_into 指向主商品），作为主商品的一个“商品页”，来源 URL / 平台 / 外部 ID 不变；
其价格记录移到主商品名下并在 listing_id 记下来源商品页；之后再采到的价格也自动记到主商品。每次合并写 product_merges 审计，可撤销。
"""
import json, time, itertools, datetime
import numpy as np
from fastapi import HTTPException, Body
from app import db
from .features import extract
DEFAULTS = {"suggest_threshold": 0.70, "same_platform": {"auto": False, "threshold": 0.97}, "cross_platform": {"auto": False, "threshold": 0.985}, "auto_detect": True,
            "blocking": {"enabled": True, "exact_max": 20000, "ann_k": 30, "max_block": 200}}
def _gs(k, d=None):
    r = db.q("SELECT v FROM settings WHERE k=?", (k,), one=True)
    try: return json.loads(r["v"]) if r else d
    except Exception: return d
def get_settings():
    s = json.loads(json.dumps(DEFAULTS)); cur = _gs("dedup", {}) or {}
    for k, v in cur.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict): s[k].update(v)
        else: s[k] = v
    return s
def put_settings(body: dict = Body(...)):
    s = get_settings()
    for k in ("suggest_threshold", "auto_detect"):
        if k in body: s[k] = body[k]
    if isinstance(body.get("blocking"), dict):
        bb = body["blocking"]; s["blocking"].update({x: (bool(bb[x]) if x == "enabled" else int(bb[x])) for x in ("enabled", "exact_max", "ann_k", "max_block") if x in bb})
        s["blocking"]["ann_k"] = max(5, min(200, s["blocking"]["ann_k"])); s["blocking"]["max_block"] = max(20, min(5000, s["blocking"]["max_block"]))
    for k in ("same_platform", "cross_platform"):
        if isinstance(body.get(k), dict): s[k].update({x: body[k][x] for x in ("auto", "threshold") if x in body[k]})
    for k in ("same_platform", "cross_platform"):
        t = float(s[k]["threshold"])
        if not 0.9 <= t <= 1.0: raise HTTPException(400, "自动合并阈值只能设在 0.90–1.00（仅高置信）")
        s[k]["threshold"] = t; s[k]["auto"] = bool(s[k]["auto"])
    s["suggest_threshold"] = max(0.5, min(0.99, float(s["suggest_threshold"])))
    db.ex("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ("dedup", json.dumps(s, ensure_ascii=False))); return s

def _active_products():
    return db.q("SELECT id,platform,title,brand,model,gtin,attrs,url,phash,clip,currency,group_key,external_id FROM products WHERE merged_into IS NULL AND parent_id IS NULL")
def _text_sim(ps):
    from app import search
    docs = [extract(p)["norm"] or (p.get("title") or "") for p in ps]
    if getattr(search, "_ST", None):
        E = search._emb(docs); return E @ E.T, "多语言向量"
    from sklearn.feature_extraction.text import TfidfVectorizer
    X = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit_transform(docs); return (X @ X.T).toarray(), "字符 n-gram"
def _img_sim(a, b):
    if a.get("clip") and b.get("clip"):
        try: return float(np.dot(json.loads(a["clip"]), json.loads(b["clip"]))), "CLIP"
        except Exception: pass
    if a.get("phash") and b.get("phash"):
        import imagehash
        return max(0.0, 1 - (imagehash.hex_to_hash(a["phash"]) - imagehash.hex_to_hash(b["phash"])) / 32), "phash"
    return None, None
def score_pair(a, b, fa, fb, text_sim, text_engine="向量"):
    why, sig = [], {"text": round(float(text_sim), 4)}
    # 条码
    for k in ("gtin", "asin"):
        if fa["ids"].get(k) and fb["ids"].get(k):
            if fa["ids"][k] == fb["ids"][k]:
                return 0.99, [f"{k.upper()} 一致（{fa['ids'][k]}）"], {**sig, k: 1}
            return 0.30, [f"{k.upper()} 不同（{fa['ids'][k]} ≠ {fb['ids'][k]}）"], {**sig, k: 0}
    def same(x, y): return None if not x or not y else (1.0 if x == y else 0.0)
    br = same(fa["brand"], fb["brand"]); md = same(fa["model"], fb["model"]); se = same(fa["series"], fb["series"])
    mod = md if md is not None else se
    cap = None if not fa["capacity"] or not fb["capacity"] else (1.0 if set(fa["capacity"]) == set(fb["capacity"]) else 0.0)
    col = same(fa["color"], fb["color"]); img, img_eng = _img_sim(a, b)
    sig.update(brand=br, model=mod, capacity=cap, color=col, image=None if img is None else round(img, 4))
    sc = 0.45 * float(text_sim) + 0.20 * (mod if mod is not None else 0.5) + 0.10 * (br if br is not None else 0.5) + 0.10 * (cap if cap is not None else 0.5) \
         + 0.05 * (col if col is not None else 0.5) + 0.10 * (img if img is not None else float(text_sim))
    why.append(f"标题相似 {float(text_sim):.0%}（{text_engine}）")
    if fa["norm"] == fb["norm"]: why.append("归一化标题完全相同"); sc = max(sc, 0.975 if float(text_sim) > 0.99 else 0.9)   # 冲突在下面统一封顶
    if br == 1: why.append(f"品牌一致（{fa['brand']}）")
    if mod == 1: why.append(f"型号一致（{fa['model'] or fa['series']}）")
    if cap == 1: why.append(f"容量/规格一致（{'/'.join(fa['capacity'])}）")
    if col == 1: why.append(f"颜色一致（{fa['color']}）")
    if img is not None: why.append(f"图片相似 {img:.0%}（{img_eng}）")
    caps = []
    cond_a, cond_b = fa.get("condition", "new"), fb.get("condition", "new")
    if cond_a != cond_b: caps.append((0.50, f"成色不同（{ {'new': '全新', 'refurbished': '福利/翻新', 'used': '二手'}[cond_a]} / { {'new': '全新', 'refurbished': '福利/翻新', 'used': '二手'}[cond_b]}）"))
    elif cond_a != "new": why.append("成色一致（" + {'refurbished': '福利/翻新', 'used': '二手'}[cond_a] + "）")
    if br == 0: caps.append((0.30, f"品牌不同（{fa['brand']} / {fb['brand']}）"))
    if cap == 0: caps.append((0.40, f"容量/规格不同（{'/'.join(fa['capacity'])} / {'/'.join(fb['capacity'])}）"))
    if mod == 0: caps.append((0.50, f"型号不同（{fa['model'] or fa['series']} / {fb['model'] or fb['series']}）"))
    if col == 0: caps.append((0.60, f"颜色不同（{fa['color']} / {fb['color']}）"))
    for c, w in caps: sc = min(sc, c); why.append("✕ " + w)
    return round(min(sc, 0.98), 4), why, sig

_BIDX = {"key": None, "index": None, "engine": None}
def _vectors(ps, F):
    """返回 (vec, engine)：有多语言模型时为 L2 归一化 dense 向量，否则为 TF-IDF 字符 n-gram 稀疏向量（行归一化）。"""
    from app import search
    docs = [f["norm"] or (p.get("title") or "") for p, f in zip(ps, F)]
    if getattr(search, "_ST", None): return np.asarray(search._emb(docs), dtype=np.float32), "多语言向量"
    from sklearn.feature_extraction.text import TfidfVectorizer
    return TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3)).fit_transform(docs).tocsr(), "字符 n-gram"
def invalidate_index():
    """商品增删改后失效去重分块索引（内存 + 磁盘 ANN）。"""
    _BIDX.update(key=None, index=None, engine=None)
    try:
        from app.core.vector import remove_index
        remove_index("dedup")
    except Exception:
        pass

def _ann_signature(ps, b, engine=""):
    from app.core.vector import content_digest, make_signature
    return make_signature(
        name="dedup", kind="auto", dim=0, n=len(ps),
        digest=content_digest([p["id"] for p in ps], texts=[p.get("title") for p in ps]),
        params={"exact_max": int(b.get("exact_max", 20000)), "ann_k": int(b.get("ann_k", 30)),
                "max_block": int(b.get("max_block", 200))},
        model=engine or "",
    )

def _save_ann(ix, ps, b, engine):
    try:
        ix.save_ann("dedup", signature=_ann_signature(ps, b, engine))
    except Exception as e:
        import logging; logging.getLogger("pricehub.dedup").warning("dedup ann save failed", extra={"error": str(e)})

def _block_index(ps, F, new_ids):
    """取得分块索引；仅新增商品时在缓存索引上增量 add（dense 向量时），否则先尝试磁盘 ANN，再重建。返回 (index, engine, incremental)。"""
    from .blocking import BlockIndex
    b = (get_settings().get("blocking") or {})
    sig = [(p["id"], p.get("title")) for p in ps]; newset = set(new_ids or [])
    old = _BIDX["key"]
    if new_ids and old is not None and _BIDX["engine"] == "多语言向量" and sig[:len(old)] == old and {x[0] for x in sig[len(old):]} <= newset and len(sig) > len(old):
        add_ps, add_F = ps[len(old):], F[len(old):]; vec, _ = _vectors(add_ps, add_F)
        _BIDX["index"].add(add_F, vec); _BIDX["key"] = sig
        _save_ann(_BIDX["index"], ps, b, _BIDX["engine"]); return _BIDX["index"], _BIDX["engine"], True
    ix = BlockIndex(exact_max=int(b.get("exact_max", 20000)), k=int(b.get("ann_k", 30)), max_block=int(b.get("max_block", 200)))
    ix.add(F, None)
    expect = _ann_signature(ps, b)
    ok, _reason = ix.load_ann("dedup", expected=expect)
    if ok and ix.vec is not None and len(ix.vec) == ix.n:
        eng = "多语言向量"
        _BIDX.update(key=sig, index=ix, engine=eng); return ix, eng, False
    ix.vec, ix.ann = None, None
    vec, eng = _vectors(ps, F)
    ix._add_vec(vec)
    _BIDX.update(key=sig, index=ix, engine=eng); _save_ann(ix, ps, b, eng); return ix, eng, False


def detect(new_ids=None, record=True):
    """检测候选。new_ids 给定时只比较这些商品与其余商品（新采集增量检测）；否则全量。返回新增/更新的候选数与自动合并数。"""
    st = get_settings(); thr = float(st["suggest_threshold"])
    ps = _active_products()
    if len(ps) < 2: return {"pairs": 0, "candidates": 0, "auto_merged": 0}
    for p in ps:
        try: p["attrs"] = json.loads(p.get("attrs") or "{}")
        except Exception: p["attrs"] = {}
    ps.sort(key=lambda p: p["id"])
    F = [extract(p) for p in ps]; idx = {p["id"]: i for i, p in enumerate(ps)}
    decided = {(r["a_id"], r["b_id"]): r["status"] for r in db.q("SELECT a_id,b_id,status FROM dedup_candidates")}
    focus = [idx[i] for i in (new_ids or []) if i in idx] if new_ids else None
    t0 = time.time()
    n = len(ps); all_pairs = n * (n - 1) // 2 if focus is None else len(focus) * (n - 1) - len(focus) * (len(focus) - 1) // 2
    found, seen, n_pairs, auto = 0, set(), 0, 0
    def _accept(i, j, sc, why, sig):
        nonlocal found, auto
        a, b = ps[i], ps[j]
        if a["id"] > b["id"]: a, b, i, j = b, a, j, i
        if (a["id"], b["id"]) in seen: return
        seen.add((a["id"], b["id"]))
        scope = "same_platform" if a["platform"] == b["platform"] else "cross_platform"
        if scope == "same_platform": why.insert(0, f"同一平台（{a['platform']}）的不同商品页")
        else: why.insert(0, f"跨平台：{a['platform']} / {b['platform']}")
        if (a["id"], b["id"]) in decided and decided[(a["id"], b["id"])] != "pending": return
        if not record: found += 1; return
        db.ex("INSERT INTO dedup_candidates(a_id,b_id,score,scope,reasons,signals,status) VALUES(?,?,?,?,?,?,'pending') "
              "ON CONFLICT(workspace_id,a_id,b_id) DO UPDATE SET score=excluded.score, reasons=excluded.reasons, signals=excluded.signals, scope=excluded.scope",
              (a["id"], b["id"], sc, scope, json.dumps(why, ensure_ascii=False), json.dumps(sig, ensure_ascii=False)))
        found += 1
        rule = st[scope]
        if rule["auto"] and sc >= rule["threshold"]:
            c = db.q("SELECT id FROM dedup_candidates WHERE a_id=? AND b_id=?", (a["id"], b["id"]), one=True)
            try: merge(c["id"], mode="auto"); auto += 1
            except HTTPException: pass
    if (st.get("blocking") or {}).get("enabled", True):
        from .parallel import score_candidate_codes
        import scipy.sparse as sp
        bix, eng, incr = _block_index(ps, F, new_ids); codes, bstats = bix.candidate_codes(focus)
        simf = bix.sim; mode = "blocking"; n_pairs = int(len(codes))
        vec = None if (bix.vec is not None and sp.issparse(bix.vec)) else bix.vec
        for i, j, sc, why, sig in score_candidate_codes(codes, n, ps, F, vec=vec, simf=simf, engine=eng, threshold=thr):
            _accept(i, j, sc, why, sig)
    else:   # v12 全比较（保留作对照）
        S, eng = _text_sim(ps); incr = False; bstats = {}; simf = lambda i, j: float(S[i, j]); mode = "all_pairs"
        pairs = ((i, j) for i in (focus if focus is not None else range(len(ps))) for j in range(len(ps)) if (focus is not None and i != j) or (focus is None and j > i))
        for i, j in pairs:
            a, b = ps[i], ps[j]
            if a["id"] > b["id"]: a, b, i, j = b, a, j, i
            if (a["id"], b["id"]) in seen: continue
            n_pairs += 1
            sij = simf(i, j)
            if sij < 0.45 and not (F[i]["ids"] and F[j]["ids"]): continue
            sc, why, sig = score_pair(a, b, F[i], F[j], sij, eng)
            if sc < thr: continue
            _accept(i, j, sc, why, sig)
    return {"pairs": n_pairs, "candidates": found, "auto_merged": auto, "engine": eng, "mode": mode, "all_pairs": all_pairs,
            "incremental": incr, "blocking": bstats, "seconds": round(time.time() - t0, 3)}

def _view(r, pm):
    def brief(pid):
        p = pm.get(pid) or {}
        return {k: p.get(k) for k in ("id", "title", "platform", "url", "currency", "merged_into")} | {"last_price": p.get("last_price"), "n_prices": p.get("n_prices", 0)}
    return {"id": r["id"], "score": r["score"], "scope": r["scope"], "status": r["status"], "reasons": json.loads(r["reasons"] or "[]"),
            "signals": json.loads(r["signals"] or "{}"), "created": r["created"], "decided_at": r["decided_at"], "a": brief(r["a_id"]), "b": brief(r["b_id"])}
def _pmap(ids):
    if not ids: return {}
    ph = ",".join("?" * len(ids))
    pm = {p["id"]: p for p in db.q(f"SELECT id,title,platform,url,currency,merged_into FROM products WHERE id IN ({ph})", list(ids))}
    for r in db.q(f"SELECT product_id, COUNT(*) n FROM prices WHERE product_id IN ({ph}) GROUP BY product_id", list(ids)): pm.get(r["product_id"], {})["n_prices"] = r["n"]
    for r in db.q(f"SELECT product_id, price FROM prices WHERE id IN (SELECT MAX(id) FROM prices WHERE product_id IN ({ph}) GROUP BY product_id)", list(ids)): pm.get(r["product_id"], {})["last_price"] = r["price"]
    return pm
def candidates(status: str = "pending", scope: str = "", limit: int = 200):
    sql = "SELECT * FROM dedup_candidates WHERE 1=1"; a = []
    if status and status != "all": sql += " AND status=?"; a.append(status)
    if scope: sql += " AND scope=?"; a.append(scope)
    rows = db.q(sql + " ORDER BY score DESC, id LIMIT ?", a + [int(limit)])
    pm = _pmap({x for r in rows for x in (r["a_id"], r["b_id"])})
    cnt = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM dedup_candidates GROUP BY status")}
    return {"items": [_view(r, pm) for r in rows], "counts": cnt, "settings": get_settings()}

def _pick_canonical(a, b):
    """主商品：价格记录更多的；一样多取更早建档（id 小）的。"""
    na = (db.q("SELECT COUNT(*) n FROM prices WHERE product_id=?", (a,), one=True) or {}).get("n", 0)
    nb = (db.q("SELECT COUNT(*) n FROM prices WHERE product_id=?", (b,), one=True) or {}).get("n", 0)
    return (a, b) if (na, -a) >= (nb, -b) else (b, a)
def merge(cid: int, canonical_id: int = None, mode="manual", actor="user"):
    c = db.q("SELECT * FROM dedup_candidates WHERE id=?", (cid,), one=True)
    if not c: raise HTTPException(404, "候选不存在")
    if c["status"] not in ("pending",): raise HTTPException(409, f"该候选已处理（{c['status']}）")
    if canonical_id and canonical_id not in (c["a_id"], c["b_id"]): raise HTTPException(400, "主商品必须是这一对中的一个")
    keep, drop = (canonical_id, c["b_id"] if canonical_id == c["a_id"] else c["a_id"]) if canonical_id else _pick_canonical(c["a_id"], c["b_id"])
    ps = {p["id"]: p for p in db.q("SELECT id, merged_into, group_key FROM products WHERE id IN (?,?)", (keep, drop))}
    if ps.get(keep, {}).get("merged_into") or ps.get(drop, {}).get("merged_into"): raise HTTPException(409, "其中一个商品已被合并到别的商品，请先撤销那次合并")
    moved = [r["id"] for r in db.q("SELECT id FROM prices WHERE product_id=?", (drop,))]
    children = [r["id"] for r in db.q("SELECT id FROM products WHERE merged_into=?", (drop,))]
    rules = [r["id"] for r in db.q("SELECT id FROM rules WHERE product_id=?", (drop,))]
    detail = {"moved_price_ids": moved, "children": children, "rules": rules, "prev_group_key": ps[drop]["group_key"], "score": c["score"], "reasons": json.loads(c["reasons"] or "[]")}
    db.ex("UPDATE prices SET listing_id=COALESCE(listing_id, ?), product_id=? WHERE product_id=?", (drop, keep, drop))
    db.ex("UPDATE products SET merged_into=?, group_key=? WHERE id=?", (keep, ps[keep]["group_key"], drop))
    if children: db.ex(f"UPDATE products SET merged_into=? WHERE id IN ({','.join('?' * len(children))})", [keep] + children)
    if rules: db.ex(f"UPDATE rules SET product_id=? WHERE id IN ({','.join('?' * len(rules))})", [keep] + rules)
    mid = db.ex("INSERT INTO product_merges(canonical_id,merged_id,candidate_id,mode,actor,detail) VALUES(?,?,?,?,?,?)", (keep, drop, cid, mode, actor, json.dumps(detail, ensure_ascii=False)))
    db.ex("UPDATE dedup_candidates SET status=?, decided_at=datetime('now') WHERE id=?", ("auto_merged" if mode == "auto" else "merged", cid))
    # 其他涉及被合并商品的待处理候选：失效（已不是独立商品）
    db.ex("UPDATE dedup_candidates SET status='superseded', decided_at=datetime('now') WHERE status='pending' AND (a_id=? OR b_id=?)", (drop, drop))
    invalidate_index()
    return {"ok": True, "merge_id": mid, "canonical_id": keep, "merged_id": drop, "moved_prices": len(moved), "message": f"已合并：保留 #{keep}，#{drop} 作为它的商品页，移动 {len(moved)} 条价格记录"}
def unmerge(mid: int):
    m = db.q("SELECT * FROM product_merges WHERE id=?", (mid,), one=True)
    if not m: raise HTTPException(404, "合并记录不存在")
    if m["undone_at"]: raise HTTPException(409, "这次合并已经撤销过")
    d = json.loads(m["detail"] or "{}"); keep, drop = m["canonical_id"], m["merged_id"]
    # 被合并商品页的价格（含合并后新采到的，listing_id=drop）全部还回去
    db.ex("UPDATE prices SET product_id=?, listing_id=NULL WHERE product_id=? AND listing_id=?", (drop, keep, drop))
    db.ex("UPDATE products SET merged_into=NULL, group_key=? WHERE id=?", (d.get("prev_group_key"), drop))
    ch = d.get("children") or []
    if ch: db.ex(f"UPDATE products SET merged_into=? WHERE id IN ({','.join('?' * len(ch))})", [drop] + ch)
    rl = d.get("rules") or []
    if rl: db.ex(f"UPDATE rules SET product_id=? WHERE id IN ({','.join('?' * len(rl))})", [drop] + rl)
    db.ex("UPDATE product_merges SET undone_at=datetime('now') WHERE id=?", (mid,))
    if m["candidate_id"]: db.ex("UPDATE dedup_candidates SET status='pending', decided_at=NULL WHERE id=?", (m["candidate_id"],))
    db.ex("UPDATE dedup_candidates SET status='pending', decided_at=NULL WHERE status='superseded' AND (a_id=? OR b_id=?)", (drop, drop))
    invalidate_index()
    return {"ok": True, "message": f"已撤销合并：#{drop} 恢复为独立商品"}
def not_same(cid: int):
    c = db.q("SELECT status FROM dedup_candidates WHERE id=?", (cid,), one=True)
    if not c: raise HTTPException(404, "候选不存在")
    db.ex("UPDATE dedup_candidates SET status='not_same', decided_at=datetime('now') WHERE id=?", (cid,)); return {"ok": True}
def reopen(cid: int):
    db.ex("UPDATE dedup_candidates SET status='pending', decided_at=NULL WHERE id=? AND status='not_same'", (cid,)); return {"ok": True}
def batch(body: dict = Body(...)):
    act = body.get("action"); ids = [int(i) for i in body.get("ids") or []]
    if act not in ("merge", "not_same"): raise HTTPException(400, "action 只能是 merge 或 not_same")
    ok, fail = [], []
    for i in ids:
        try: (merge(i) if act == "merge" else not_same(i)); ok.append(i)
        except HTTPException as e: fail.append({"id": i, "error": e.detail})
    return {"ok": ok, "failed": fail, "message": f"{'合并' if act == 'merge' else '标为不同'} {len(ok)} 条" + (f"，{len(fail)} 条未处理" if fail else "")}
def merges(limit: int = 100):
    rows = db.q("SELECT * FROM product_merges ORDER BY id DESC LIMIT ?", (int(limit),))
    pm = _pmap({x for r in rows for x in (r["canonical_id"], r["merged_id"])})
    return {"items": [{**{k: r[k] for k in ("id", "canonical_id", "merged_id", "candidate_id", "mode", "actor", "ts", "undone_at")},
                       "detail": json.loads(r["detail"] or "{}"), "canonical": pm.get(r["canonical_id"]), "merged": pm.get(r["merged_id"])} for r in rows]}
def listings(pid: int):
    """主商品的所有商品页（含自身），每个保留原始平台与 URL。"""
    rows = db.q("SELECT id,platform,url,external_id,title,seller FROM products WHERE id=? OR merged_into=? ORDER BY id", (pid, pid))
    for r in rows:
        r["n_prices"] = (db.q("SELECT COUNT(*) n FROM prices WHERE product_id=? AND COALESCE(listing_id, product_id)=?", (pid, r["id"]), one=True) or {}).get("n", 0)
        r["canonical"] = r["id"] == pid
    return {"product_id": pid, "listings": rows}
def scan(body: dict = Body(None)):
    b = body or {}; t0 = time.time(); r = detect(b.get("ids")); r["ms"] = int((time.time() - t0) * 1000); return r
def on_new_products(ids):
    """采集完成后调用：增量检测新商品。失败不影响采集。"""
    try:
        if ids and get_settings().get("auto_detect", True): return detect(list({int(i) for i in ids}))
    except Exception as e:
        import logging; logging.getLogger("pricehub.dedup").warning("dedup detect failed", extra={"error": str(e)})
