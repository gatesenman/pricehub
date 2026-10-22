"""v12：同款去重——属性抽取、打分与解释、复核队列、合并（价格与商品页挂到主商品、保留来源 URL、审计）、撤销、批量、
自动合并阈值（跨平台默认关闭）、新采集商品自动检测。"""
import os, sys, time, json, tempfile, subprocess, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _wait, _env

def test_extract_and_score_unit():
    from app.domains.dedup.features import extract
    from app.domains.dedup.service import score_pair
    a = extract({"title": "Apple 蘋果 iPhone 17e (256G)"}); assert a["brand"] == "apple" and a["series"] == "iphone 17 e" or a["series"].startswith("iphone 17")
    assert a["capacity"] == ["256gb"] and a["condition"] == "new"
    assert extract({"title": "A4 复印纸 70g 5包"})["capacity"] == ["70g"]
    assert extract({"title": "Apple 蘋果 【A++級福利品】iPhone 17 256G"})["condition"] == "refurbished"
    assert extract({"title": "苹果 iPhone 17 Pro 1TB 黑色"})["capacity"] == ["1024gb"] and extract({"title": "苹果 iPhone 17 Pro 1TB 黑色"})["color"] == "black"
    p1, p2 = {"title": "Apple iPhone 17 Pro 256GB 黑色"}, {"title": "苹果 iPhone 17 Pro 512GB 黑色"}
    sc, why, _ = score_pair(p1, p2, extract(p1), extract(p2), 0.95); assert sc <= 0.40 and any("容量" in w for w in why)
    p3 = {"title": "Apple 蘋果 【福利品】iPhone 17 Pro 256G"}
    sc, why, _ = score_pair(p1, p3, extract(p1), extract(p3), 0.95); assert sc <= 0.5 and any("成色" in w for w in why)
    g1, g2 = {"title": "x", "gtin": "4901234567894"}, {"title": "y 完全不同", "gtin": "04901234567894"}
    sc, why, _ = score_pair(g1, g2, extract(g1), extract(g2), 0.1); assert sc == 0.99 and "GTIN" in why[0]
    g3 = {"title": "x", "attrs": {"asin": "B0ABCDEFGH"}}; g4 = {"title": "x", "attrs": {"asin": "B0ZZZZZZZZ"}}
    sc, why, _ = score_pair(g3, g4, extract(g3), extract(g4), 1.0); assert sc == 0.30

@pytest.fixture(scope="module")
def api():
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8988"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory"))
    _wait("http://127.0.0.1:8988/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8988", timeout=180); yield c; p.terminate()
def _mk(c, title, platform, ext, prices, url=None, **kw):
    pid = c.post("/api/products", json={"title": title, "platform": platform, "external_id": ext, "url": url or f"https://{platform}/p/{ext}", **kw}).json()["id"]
    for i, pr in enumerate(prices): c.post(f"/api/products/{pid}/prices", json={"price": pr, "ts": f"2026-10-0{i+1} 08:00:00", "method": "manual"})
    return pid

def test_queue_merge_undo(api):
    c = api
    a = _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "dA", [25900, 25500, 25900], url="https://24h.pchome.com.tw/prod/A")
    b = _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "dB", [25900], url="https://24h.pchome.com.tw/prod/B")
    v = _mk(c, "Apple 蘋果 iPhone 17e (512G)", "24h.pchome.com.tw", "dC", [30900])
    x = _mk(c, "iPhone 17e 256GB", "shopee.tw", "dX", [24990])
    r = c.post("/api/dedup/scan", json={}).json(); assert r["candidates"] >= 2 and r["auto_merged"] == 0
    q = c.get("/api/dedup/candidates").json(); pairs = {(i["a"]["id"], i["b"]["id"]): i for i in q["items"]}
    ab = pairs[(a, b)]; assert ab["scope"] == "same_platform" and ab["score"] >= 0.95 and any("不同商品页" in w for w in ab["reasons"])
    assert (a, v) not in pairs and (b, v) not in pairs        # 容量不同，不进队列
    assert pairs.get((a, x), {}).get("scope", "cross_platform") == "cross_platform"
    m = c.post(f"/api/dedup/candidates/{ab['id']}/merge", json={}).json()
    assert m["canonical_id"] == a and m["merged_id"] == b and m["moved_prices"] == 1   # 价格多的为主
    assert b not in {p["id"] for p in c.get("/api/products").json()}                  # 被合并的商品不再单独显示
    h = c.get(f"/api/products/{a}/history").json(); assert len(h["history"]) == 4
    ls = c.get(f"/api/products/{a}/listings").json()["listings"]
    assert {l["id"] for l in ls} == {a, b} and next(l for l in ls if l["id"] == b)["url"].endswith("/prod/B")   # 来源 URL 保留
    # 被合并商品页之后再采到的价格，记到主商品名下
    c.post("/api/products", json={"title": "Apple 蘋果 iPhone 17e (256G)", "platform": "24h.pchome.com.tw", "external_id": "dB", "price": 24900})
    assert len(c.get(f"/api/products/{a}/history").json()["history"]) == 5
    log = c.get("/api/dedup/merges").json()["items"]; assert log[0]["merged_id"] == b and log[0]["mode"] == "manual" and log[0]["detail"]["reasons"]
    # 撤销：价格（含合并后新采到的）全部还给 b
    assert c.post(f"/api/dedup/merges/{log[0]['id']}/undo").json()["ok"]
    assert len(c.get(f"/api/products/{a}/history").json()["history"]) == 3 and len(c.get(f"/api/products/{b}/history").json()["history"]) == 2
    assert b in {p["id"] for p in c.get("/api/products").json()}
    assert c.post(f"/api/dedup/merges/{log[0]['id']}/undo").status_code == 409
    assert any(i["id"] == ab["id"] for i in c.get("/api/dedup/candidates").json()["items"])   # 回到待处理

def test_not_same_and_batch(api):
    c = api
    q = c.get("/api/dedup/candidates").json()["items"]; first = q[0]
    assert c.post(f"/api/dedup/candidates/{first['id']}/not_same").json()["ok"]
    c.post("/api/dedup/scan", json={})
    assert first["id"] not in {i["id"] for i in c.get("/api/dedup/candidates").json()["items"]}   # 重扫后不再出现
    assert c.get("/api/dedup/candidates?status=not_same").json()["items"][0]["id"] == first["id"]
    rest = [i["id"] for i in c.get("/api/dedup/candidates").json()["items"]]
    if rest:
        r = c.post("/api/dedup/batch", json={"action": "not_same", "ids": rest}).json(); assert len(r["ok"]) == len(rest)
    assert c.post("/api/dedup/batch", json={"action": "delete", "ids": [1]}).status_code == 400

def test_settings_and_auto_merge(api):
    c = api
    s = c.get("/api/dedup/settings").json(); assert s["cross_platform"]["auto"] is False and s["same_platform"]["auto"] is False
    assert c.put("/api/dedup/settings", json={"cross_platform": {"auto": True, "threshold": 0.5}}).status_code == 400   # 只允许高置信
    c.put("/api/dedup/settings", json={"same_platform": {"auto": True, "threshold": 0.97}})
    a = _mk(c, "Sony WH-1000XM6 降噪耳机 黑色", "jd.com", "sx1", [2999, 2899]); b = _mk(c, "Sony WH-1000XM6 降噪耳机 黑色", "jd.com", "sx2", [2999])
    x = _mk(c, "索尼 WH-1000XM6 无线降噪耳机 黑色", "tmall.com", "sx3", [2899])
    r = c.post("/api/dedup/scan", json={"ids": [a, b, x]}).json(); assert r["auto_merged"] >= 1
    log = c.get("/api/dedup/merges").json()["items"]; auto = [m for m in log if m["mode"] == "auto"]
    assert auto and {auto[0]["canonical_id"], auto[0]["merged_id"]} == {a, b}
    assert all(m["merged_id"] != x and m["canonical_id"] != x for m in log)   # 跨平台没有被自动合并
    c.put("/api/dedup/settings", json={"same_platform": {"auto": False}})

def test_auto_detect_on_collection(api):
    c = api
    s1 = c.post("/api/sources", json={"name": "dd-淘宝", "type": "mock", "config": {"platform": "taobao.com", "seed": 11}}).json()["id"]
    s2 = c.post("/api/sources", json={"name": "dd-京东", "type": "mock", "config": {"platform": "jd.com", "seed": 12}}).json()["id"]
    n0 = sum(c.get("/api/dedup/candidates?status=all").json()["counts"].values())
    c.post(f"/api/sources/{s1}/fetch"); c.post(f"/api/sources/{s2}/fetch")
    cs = c.get("/api/dedup/candidates?scope=cross_platform").json()["items"]
    assert sum(c.get("/api/dedup/candidates?status=all").json()["counts"].values()) > n0
    assert any({i["a"]["platform"], i["b"]["platform"]} == {"taobao.com", "jd.com"} for i in cs)
