"""v5 接口 e2e：无头渲染采集、库存/多规格、汇率到手价、告警规则+通知渠道（本地模拟接收端）、同款匹配、买入建议、MAP 取证、
卖家画像、定价规则与利润模拟、报表、令牌权限与审计、备份恢复、代理设置、书签。"""
import os, sys, time, tempfile, subprocess, sqlite3, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8932", "http://127.0.0.1:8931"
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def c():
    env = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db"), PRICEHUB_NO_CLIP="1"); env["PRICEHUB_EMB_CACHE"] = env["PRICEHUB_DB"] + ".npz"
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8931"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8932"], cwd=ROOT, env=env)]
    wait(MOCK + "/docs"); wait(API + "/api/health")
    cl = httpx.Client(base_url=API, timeout=120); yield cl
    for p in ps: p.terminate()
def task(c, t):
    for _ in range(120):
        r = c.get(f"/api/tasks/{t['id']}").json()
        if r["status"] in ("已完成", "失败"): return r
        time.sleep(0.5)
    raise RuntimeError("task timeout")
def test_headless_render_collect(c):
    r = c.post("/api/render/test", json={"url": MOCK + "/shop/spa-price", "force": False}).json()
    assert r["ok"] and r["item"]["price"] == 2099 and r["item"]["rendered"] and "无头浏览器" in r["detail"]
    t = task(c, c.post("/api/quick-monitor", json={"url": MOCK + "/shop/spa-price"}).json())
    assert t["status"] == "已完成" and t["result"]["rendered"] and t["result"]["price"] == 2099
    r = c.post("/api/render/test", json={"url": MOCK + "/shop/blocked"}).json(); assert not r["ok"] and r["status"] == "反爬拦截"
def test_stock_variants_and_channels_rules(c):
    c.delete(MOCK + "/hooks"); c.post(MOCK + "/shop/stock/set", params={"state": "in", "price": "399.00"})
    chs = {}
    for t, url, cfg in [("webhook", MOCK + "/hooks/erp", {"events": ["price_change", "price_suggestion"]}), ("wecom", MOCK + "/hooks/wecom", {}), ("feishu", MOCK + "/hooks/feishu", {}),
                        ("dingtalk", MOCK + "/hooks/dingtalk", {}), ("telegram", MOCK + "/tg/bot123", {"chat_id": "42"})]:
        chs[t] = c.post("/api/channels", json={"name": t, "type": t, "url": url, "config": cfg}).json()["id"]
    assert c.post("/api/channels", json={"type": "wecom", "url": "ftp://x"}).status_code == 400
    assert c.post(f"/api/channels/{chs['feishu']}/test").json()["ok"]
    assert httpx.get(MOCK + "/hooks/feishu").json()[0]["msg_type"] == "text"
    t = task(c, c.post("/api/quick-monitor", json={"url": MOCK + "/shop/stock"}).json()); pid = t["result"]["product_id"]
    full = c.get(f"/api/products/{pid}/full").json(); assert full["in_stock"] == 1 and len(full["variants"]) == 2 and full["gtin"] == "0000000000017"
    ids = list(chs.values())
    c.post("/api/rules", json={"type": "stock", "product_id": pid, "channel_ids": ids, "cooldown_min": 0})
    c.post("/api/rules", json={"type": "drop_pct", "value": 10, "product_id": pid, "channel_ids": [chs["wecom"]], "cooldown_min": 0})
    c.post(MOCK + "/shop/stock/set", params={"state": "out", "price": "299.00"})
    sid = t["result"]["source_id"]; fr = c.post(f"/api/sources/{sid}/fetch"); assert fr.status_code == 200, fr.text
    w = httpx.get(MOCK + "/hooks/wecom").json(); assert any("缺货" in m["text"]["content"] for m in w) and any("降价" in m["text"]["content"] for m in w)
    assert any("缺货" in m["content"]["text"] for m in httpx.get(MOCK + "/hooks/feishu").json())
    assert any("缺货" in m["text"]["content"] for m in httpx.get(MOCK + "/hooks/dingtalk").json())
    tg = httpx.get(MOCK + "/hooks/telegram").json(); assert tg and tg[-1]["chat_id"] == "42"
    erp = httpx.get(MOCK + "/hooks/erp").json(); assert any(e.get("event") == "price_change" for e in erp) and any(e.get("event") == "alert" for e in erp)
    assert len(c.get("/api/notifications").json()) >= 6 and all(n["ok"] for n in c.get("/api/notifications").json())
    assert any("缺货" in a["msg"] for a in c.get("/api/alerts").json())
def test_fx_landed_and_target(c):
    t = task(c, c.post("/api/quick-monitor", json={"url": MOCK + "/shop/usd"}).json()); pid = t["result"]["product_id"]
    p = c.put(f"/api/products/{pid}/costs", json={"tax_rate": 0.1, "coupon": 5, "target_price": 300}).json()
    L = p["landed"]; assert L["shipping"] == 15 and L["landed"] == round((329.99 + 15 - 5) * 1.1, 2) and L["landed_cny"] == round(L["landed"] * 7.12, 2)
    c.post("/api/fx", json={"rates": {"USD": 7.0}}); assert c.get(f"/api/products/{pid}/full").json()["landed"]["landed_cny"] == round(L["landed"] * 7.0, 2)
    assert any(r["type"] == "target" for r in p["rules"])
    c.post(f"/api/products/{pid}/prices", json={"price": 299}); assert any("目标价" in a["msg"] for a in c.get("/api/alerts").json())
def test_match_merge_and_advice(c):
    for n, cfg in [("淘宝", {"platform": "taobao.com", "seed": 1}), ("京东", {"platform": "jd.com", "bias": 1.08, "seed": 2})]:
        sid = c.post("/api/sources", json={"name": n, "type": "mock", "config": cfg}).json()["id"]; c.post(f"/api/sources/{sid}/fetch")
    sony = [p for p in c.get("/api/products").json() if "Switch" in p["title"]]
    sug = c.get("/api/match/suggest", params={"threshold": 0.7}).json()
    assert any({s["a"]["id"], s["b"]["id"]} & {p["id"] for p in sony} for s in sug), (sony, sug[:3]); pair = next(s for s in sug if {s["a"]["id"], s["b"]["id"]} & {p["id"] for p in sony}); assert any("型号" in w or "标题" in w for w in pair["why"])
    r = c.post("/api/match/merge", json={"ids": [pair["a"]["id"], pair["b"]["id"]]}).json(); assert r["undo"]
    gk = {p["id"]: p["group_key"] for p in c.get("/api/products").json()}; assert gk[pair["a"]["id"]] == gk[pair["b"]["id"]]
    pid = sony[0]["id"]
    for i, v in enumerate([2600, 2550, 2500, 2480]): c.post(f"/api/products/{pid}/prices", json={"price": v, "ts": f"2026-09-0{i+1} 10:00:00"})
    a = c.get(f"/api/products/{pid}/advice").json(); assert a["verdict"] in ("现在买", "可以买", "再等等", "观望") and a["reasons"] and a["conf"]["level"]
    cal = c.get("/api/promo/calendar").json(); assert any(e["name"] == "双 11" for e in cal) and any("黑色星期五" == e["name"] for e in cal)
def test_map_evidence_sellers(c):
    ps = c.get("/api/products").json(); p = next(x for x in ps if x["platform"] == "jd.com" and x.get("last_price"))
    c.post("/api/map/prices", json={"group_key": p["group_key"], "price": p["last_price"] * 1.2})
    v = c.get("/api/map/violations").json(); assert any(x["id"] == p["id"] for x in v)
    t = task(c, c.post(f"/api/map/evidence/{p['id']}").json()); assert t["status"] == "已完成", t
    ev = c.get("/api/map/evidence").json()[0]; img = c.get(f"/api/map/evidence/{ev['id']}/image"); assert img.status_code == 200 and img.content[:4] == b"\x89PNG" and len(ev["sha256"]) == 64
    s = c.get("/api/sellers").json(); assert s[0]["violations"] >= 1 and s[0]["tag"] == "低价/乱价风险"
def test_pricing_engine_and_report(c):
    r = c.post("/api/pricing/rules", json={"name": "比最低低 2%", "strategy": "beat_lowest", "param": 2, "min_margin": 0.1, "round_to": ".9"}).json()
    ps = c.get("/api/products").json(); pid = next(p["id"] for p in ps if p["platform"] == "taobao.com")
    c.put(f"/api/products/{pid}/costs", json={"cost": 1000})
    sim = c.post("/api/pricing/simulate", json={"rule_id": r["id"], "ids": [pid], "elasticity": 1.5, "fee_rate": 0.05}).json()
    row = sim["rows"][0]; assert str(row["suggested"]).endswith(".9") and row["why"] and "profit_new" in row and sim["delta"] is not None
    assert c.post("/api/pricing/push", json={"rule_id": r["id"], "ids": [pid]}).json()["ok"]
    assert any(e.get("event") == "price_suggestion" for e in httpx.get(MOCK + "/hooks/erp").json())
    assert "同款" in c.get("/api/reports/summary", params={"format": "csv"}).text and "<table>" in c.get("/api/reports/summary", params={"format": "html"}).text
    wid = c.get("/api/channels").json()["items"][1]["id"]; c.post("/api/reports/schedule", json={"enabled": True, "hour": 9, "channel_ids": [wid]})
    assert c.post("/api/reports/send").json()["sent"] == 1
def test_tokens_roles_audit_backup(c):
    v = c.post("/api/tokens", json={"name": "看板", "role": "viewer"}).json()["token"]; a = c.post("/api/tokens", json={"name": "运维", "role": "admin"}).json()["token"]
    assert c.get("/api/products", headers={"X-API-Key": v}).status_code == 200
    assert c.post("/api/products", json={"title": "x"}, headers={"X-API-Key": v}).status_code == 403
    assert c.get("/api/products", headers={"X-API-Key": "bad"}).status_code == 401
    assert c.get("/api/backup", headers={"X-API-Key": v}).status_code == 403
    b = c.get("/api/backup", headers={"X-API-Key": a}); assert b.status_code == 200 and b.content[:15] == b"SQLite format 3"
    n0 = len(c.get("/api/products").json()); c.post("/api/products", json={"title": "备份后新增", "platform": "x"}); assert len(c.get("/api/products").json()) == n0 + 1
    r = c.post("/api/restore", files={"file": ("b.db", b.content)}).json(); assert r["ok"]; assert len(c.get("/api/products").json()) == n0
    assert c.post("/api/restore", files={"file": ("x.db", b"not a db")}).status_code == 400
    au = c.get("/api/audit").json(); assert any("令牌:看板" in x["actor"] and x["status"] == 403 for x in au) and any(x["path"] == "/api/restore" for x in au)
def test_collect_settings_bookmarklet(c):
    assert c.post("/api/collect/settings", json={"proxies": ["bad-proxy"]}).status_code == 400
    s = c.post("/api/collect/settings", json={"proxies": [], "render_fallback": True}).json(); assert s["render"]["ok"] in (True, None)
    assert "收藏到 PriceHub" in c.get("/bookmarklet").text and "?monitor=" in c.get("/bookmarklet").text
