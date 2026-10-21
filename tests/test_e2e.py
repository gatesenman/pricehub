import os, sys, time, subprocess, tempfile, io, httpx, pytest
from PIL import Image, ImageDraw
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8902", "http://127.0.0.1:8901"
def wait(u, t=180):
    for _ in range(t):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError("timeout " + u)
@pytest.fixture(scope="module")
def c():
    env = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db")); env.pop("DEEPSEEK_API_KEY", None)
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8901"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8902"], cwd=ROOT, env=env)]
    wait(MOCK + "/docs"); wait(API + "/api/health")
    yield httpx.Client(base_url=API, timeout=60)
    for p in ps: p.terminate()
def add(c, name, type_, cfg):
    r = c.post("/api/sources", json={"name": name, "type": type_, "config": cfg}); assert r.status_code == 200; sid = r.json()["id"]
    f = c.post(f"/api/sources/{sid}/fetch"); assert f.status_code == 200, f.text; return f.json()
def test_01_health_and_platform_catalog(c):
    h = c.get("/api/health").json(); print(h); assert {"mock", "webpage", "rest", "csv"} <= set(h["adapters"]); assert h["platform_catalog"] > 700
    jp = c.get("/api/platforms", params={"country": "日本"}).json(); assert jp["total"] >= 3; print("日本平台:", [p["name"] for p in jp["items"]])
def test_02_mock_sources_two_platforms(c):
    assert add(c, "淘宝mock", "mock", {"platform": "taobao.com", "seed": 1})["fetched"] == 5
    assert add(c, "京东mock", "mock", {"platform": "jd.com", "bias": 1.1, "seed": 2})["fetched"] == 5
def test_03_webpage_adapter_with_image(c):
    r = add(c, "卡网页", "webpage", {"urls": [MOCK + "/item/3"], "price_selector": ".price", "platform": "kawang", "category": "虚拟商品", "chain": "数字内容>游戏点卡"})
    p = [x for x in c.get("/api/products", params={"platform": "kawang"}).json()][0]; assert p["last_price"] == 98.0 and p["phash"]
def test_04_private_rest_api(c):
    cfg = {"url": MOCK + "/v1/goods", "headers": {"X-API-Key": "${MOCK_KEY}"}, "items_path": "data.items", "platform": "private-erp",
           "mapping": {"external_id": "sku", "title": "name", "price": "pricing.amount", "category": "cat", "chain": "chain"}}
    os.environ["MOCK_KEY"] = "secret-123"  # 服务进程读不到，故直接写明文验证 + 鉴权失败用例
    cfg["headers"] = {"X-API-Key": "secret-123"}; assert add(c, "私有API", "rest", cfg)["fetched"] == 3
    bad = dict(cfg, headers={"X-API-Key": "wrong"}); sid = c.post("/api/sources", json={"name": "坏key", "type": "rest", "config": bad}).json()["id"]
    r = c.post(f"/api/sources/{sid}/fetch"); print("bad key ->", r.status_code, r.json()); assert r.status_code == 502 and "401" in r.text
def test_05_csv_import(c):
    assert add(c, "CSV导入", "csv", {"path": os.path.join(ROOT, "data", "sample_products.csv")})["fetched"] == 6
def test_06_history_monitor_forecast(c):
    pid = c.get("/api/products", params={"platform": "private-erp"}).json()[0]["id"]
    for i, pr in enumerate([2000, 2010, 2025, 2040, 2060]): c.post(f"/api/products/{pid}/prices", json={"price": pr, "ts": f"2026-10-0{i+1} 00:00:00"})
    c.post("/api/monitor/run")
    h = c.get(f"/api/products/{pid}/history").json(); assert h["stats"]["count"] >= 7; print("history", h["stats"])
    sid = c.get("/api/products", params={"platform": "taobao.com"}).json()[0]["id"]
    for i, pr in enumerate([100, 102, 104, 106, 108]): c.post(f"/api/products/{sid}/prices", json={"price": pr, "ts": f"2026-09-0{i+1} 00:00:00"})
    f = c.get(f"/api/products/{sid}/forecast").json(); print("forecast", f); assert len(f["linear_regression"]) == 7 and f["moving_average"] > 0
def test_07_compare_and_pricing(c):
    p = [x for x in c.get("/api/products", params={"platform": "taobao.com"}).json() if "小米" in x["title"]][0]
    cmp = c.get(f"/api/products/{p['id']}/compare").json(); print("compare", cmp); assert {o["platform"] for o in cmp["offers"]} >= {"taobao.com", "jd.com"}
    pr = c.get(f"/api/products/{p['id']}/pricing", params={"cost": 5000}).json(); print("pricing", pr); assert pr["cost_plus"] == 6000 and pr["recommended"] >= 5250
def test_08_structured_searches(c):
    assert all(x["chain"].startswith("消费电子") for x in c.get("/api/search/chain", params={"chain": "消费电子"}).json())
    assert len(c.get("/api/search/category", params={"category": "耳机"}).json()) == 3
    assert any("Steam" in x["title"] for x in c.get("/api/search/keyword", params={"q": "Steam"}).json())
@pytest.mark.parametrize("q,expect", [("游戏机", ["Switch", "ゲーム機"]), ("headphones", ["耳机", "イヤホン", "Headphones"]),
                                      ("toothbrush", ["歯ブラシ"]), ("green tea", ["绿茶"]), ("キーボード", ["Keyboard"]), ("手机", ["iPhone", "小米"])])
def test_09_semantic_cross_language(c, q, expect):
    r = c.get("/api/search/semantic", params={"q": q, "k": 3}).json(); top = [x["title"] for x in r["results"]]
    print(q, "->", top, r["engine"]); assert any(e in t for t in top for e in expect)
def test_10_image_search(c):
    im = Image.new("RGB", (128, 128), "white"); ImageDraw.Draw(im).ellipse([22, 22, 106, 106], fill=(230, 10, 10)); b = io.BytesIO(); im.save(b, "PNG")
    r = c.post("/api/search/image", files={"file": ("q.png", b.getvalue(), "image/png")}).json(); print(r["engine"], r["results"][:1])
    assert r["results"][0]["platform"] == "kawang"
def test_11_ai_search_degraded(c):
    r = c.get("/api/search/ai", params={"q": "iPhone 16 最低价", "provider": "deepseek"}).json(); print(r)
    assert r["degraded"] and "DEEPSEEK_API_KEY" in r["message"] and r["results"] == []
def test_12_ai_search_top10_with_fake_provider(c):
    sys.path.insert(0, ROOT); from app import ai_search
    os.environ["DEEPSEEK_API_KEY"] = "test"; os.environ["DEEPSEEK_BASE_URL"] = MOCK + "/fake-llm/chat/completions"
    try: r = ai_search.ai_search("deepseek", "iPhone 16")
    finally: os.environ.pop("DEEPSEEK_API_KEY"); os.environ.pop("DEEPSEEK_BASE_URL")
    print("fake deepseek returned", len(r["results"]), "items, rank1:", r["results"][0]); assert r["ok"] and len(r["results"]) == 10 and len(ai_search.PROVIDERS) >= 5
def test_13_ui(c):
    assert "PriceHub" in c.get("/").text
def test_14_confidence_on_all_prices(c):
    ps = c.get("/api/products").json(); byp = {p["platform"]: p for p in ps}
    api_p, mock_p, web_p = byp["private-erp"], byp["taobao.com"], byp["kawang"]
    for p in ps: assert set(p["last_conf"]) >= {"confidence", "level", "basis"} and p["last_conf"]["level"] in "高中低"
    print("conf api/mock/web:", api_p["last_conf"]["confidence"], mock_p["last_conf"]["confidence"], web_p["last_conf"]["confidence"])
    assert web_p["last_conf"]["confidence"] < mock_p["last_conf"]["confidence"]   # CSS 抓取 < 结构化
    assert any("页面位置规则" in b for b in web_p["last_conf"]["basis"]) and any("新鲜度" in b for b in web_p["last_conf"]["basis"])
    h = c.get(f"/api/products/{mock_p['id']}/history").json()["history"]; assert all(x["conf"]["confidence"] is not None for x in h)
    r = c.post(f"/api/products/{mock_p['id']}/prices", json={"price": mock_p["last_price"] * 3}).json(); print("anomaly:", r["conf"])
    assert r["conf"]["level"] == "低" and any("异常" in b for b in r["conf"]["basis"]) and any("手动" in b for b in r["conf"]["basis"])
    f = c.get(f"/api/products/{mock_p['id']}/forecast").json(); assert "走势吻合度" in f["conf"]["basis"][0]
    cmp = c.get(f"/api/products/{mock_p['id']}/compare").json(); assert all("conf" in o for o in cmp["offers"])
    pr = c.get(f"/api/products/{mock_p['id']}/pricing", params={"cost": 5000}).json(); print("pricing conf:", pr["conf"]); assert pr["conf"]["confidence"] > 0.5
def test_15_deep_compare(c):
    ps = [p for p in c.get("/api/products").json() if "小米" in p["title"]]; ids = ",".join(str(p["id"]) for p in ps)
    d = c.get("/api/compare/deep", params={"ids": ids}).json(); print("deep:", d["diff"], d["rank"]["last_price"], d["attr_keys"])
    assert d["same_group"] and d["diff"]["platform"] and len(d["items"]) == 2 and d["rank"]["last_price"]["best"] is not None
    assert all(it["product"]["seller"] and it["history"] and "conf" in it["forecast"] for it in d["items"])
    assert c.get("/api/compare/deep", params={"ids": "1"}).status_code == 400
def test_16_ai_results_have_confidence(c):
    sys.path.insert(0, ROOT); from app import main as M
    os.environ["DEEPSEEK_API_KEY"] = "t"; os.environ["DEEPSEEK_BASE_URL"] = MOCK + "/fake-llm/chat/completions"
    try: r = M.s_ai("iPhone")
    finally: os.environ.pop("DEEPSEEK_API_KEY"); os.environ.pop("DEEPSEEK_BASE_URL")
    assert len(r["results"]) == 10 and all(x["conf"]["level"] == "低" and any("AI 搜索" in b for b in x["conf"]["basis"]) for x in r["results"])
def test_17_migration_backfills_old_db():
    import sqlite3
    path = tempfile.mktemp(suffix=".db"); con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE products(id INTEGER PRIMARY KEY, source_id INTEGER, platform TEXT, external_id TEXT, title TEXT, category TEXT, chain TEXT, brand TEXT, url TEXT,
      image_path TEXT, phash TEXT, clip TEXT, currency TEXT DEFAULT 'CNY', cost REAL, group_key TEXT, monitored INTEGER DEFAULT 1, UNIQUE(platform, external_id));
      CREATE TABLE prices(id INTEGER PRIMARY KEY, product_id INTEGER, price REAL, ts TEXT DEFAULT (datetime('now')));
      INSERT INTO products(id,platform,external_id,title) VALUES(1,'x','1','旧商品');
      INSERT INTO prices(product_id,price,ts) VALUES(1,100,'2026-01-01 00:00:00'),(1,101,'2026-01-02 00:00:00'),(1,300,'2026-01-03 00:00:00');""")
    con.commit(); con.close()
    out = subprocess.run([PY, "-c", "from app import main as M; print(M.MIGRATED)"], cwd=ROOT, env=dict(os.environ, PRICEHUB_DB=path, PRICEHUB_NO_ST="1", PRICEHUB_NO_CLIP="1"), capture_output=True, text=True)
    print("migrated:", out.stdout.strip()); assert out.stdout.strip().endswith("3")
    rows = sqlite3.connect(path).execute("SELECT price, confidence, conf_level, method FROM prices ORDER BY id").fetchall(); print(rows)
    assert all(r[1] is not None and r[3] == "manual" for r in rows) and rows[2][2] == "低"
MAJOR = ["amazon", "ebay", "aliexpress", "taobao", "tmall", "jd", "pdd", "rakuten", "mercari", "shopee", "lazada", "mercadolibre", "walmart", "coupang", "flipkart", "allegro", "ozon", "wildberries"]
def test_18_all_920_platforms_builtin(c):
    st = c.get("/api/platforms/stats").json(); print("platform stats:", st["total"], st["by_strategy"], st["by_status"][:4])
    assert st["total"] == 920
    allp = c.get("/api/platforms", params={"limit": 2000}).json(); assert allp["total"] == 920 and len(allp["items"]) == 920
    keys = {p["strategy"].split(":")[-1] for p in allp["items"] if p["strategy"].startswith("专属")}; print("专属规则平台:", sorted(keys))
    assert set(MAJOR) <= keys | {"pdd"} and all(p["search_url"] for p in allp["items"] if p["domain"])
    jp = [p for p in allp["items"] if p["country"] == "日本"]; assert all(p["currency"] == "JPY" and p["lang"] == "ja" for p in jp)
def test_19_enable_platforms(c):
    pf = c.get("/api/platforms", params={"q": "Rakuten", "country": "日本"}).json()["items"][0]
    r = c.post(f"/api/platforms/{pf['id']}/enable").json(); s = [x for x in c.get("/api/sources").json() if x["id"] == r["source_id"]][0]
    assert s["type"] == "platform" and s["enabled"] == 1
    n = c.post("/api/platforms/bulk_enable", json={"country": "日本"}).json()["enabled"]; assert n >= 6
    assert c.get("/api/platforms/stats").json()["enabled"] >= 6
    c.post(f"/api/platforms/{pf['id']}/disable"); assert c.get("/api/platforms", params={"q": "Rakuten", "country": "日本"}).json()["items"][0]["enabled"] == 0
    assert c.post("/api/monitor/run").status_code == 200   # 空配置平台源不影响监控
def test_20_generic_parser_and_confidence(c):
    pf = c.get("/api/platforms", params={"strategy": "通用", "limit": 1}).json()["items"][0]
    cfg = {"platform_slug": pf["slug"], "urls": [MOCK + "/shop/jsonld", MOCK + "/shop/microdata", MOCK + "/shop/meta", MOCK + "/shop/regex"], "category": "数码"}
    r = add(c, "通用平台测试", "platform", cfg); assert r["fetched"] == 4
    got = {p["title"]: p for p in c.get("/api/products", params={"platform": pf["domain"]}).json()}; print({k: (v["last_price"], v["last_conf"]["confidence"]) for k, v in got.items()})
    assert got["Sony WH-1000XM5 Headphones"]["last_price"] == 2499 and got["AirPods Pro 2"]["last_price"] == 1799 and got["Kindle Paperwhite"]["last_price"] == 1068
    m = {t: v["last_conf"]["confidence"] for t, v in got.items()}
    assert m["Sony WH-1000XM5 Headphones"] > m["Kindle Paperwhite"] > m["AirPods Pro 2"] > m["Xbox 手柄"]   # jsonld > microdata > meta > regex
    r = add(c, "通用平台搜索", "platform", {"platform_slug": pf["slug"], "query": "耳机", "search_url": MOCK + "/shop/search?q={q}"}); assert r["fetched"] == 4
    sid = c.post("/api/sources", json={"name": "反爬页", "type": "platform", "config": {"platform_slug": pf["slug"], "urls": [MOCK + "/shop/blocked"]}}).json()["id"]
    rr = c.post(f"/api/sources/{sid}/fetch"); print("blocked:", rr.json()); assert rr.status_code == 502 and "反爬" in rr.text
def test_21_healthcheck_classifier():
    sys.path.insert(0, ROOT); from app import parsing
    res = {k: parsing.classify(r.status_code, r.text)[0] for k, r in ((k, httpx.get(MOCK + "/shop/" + k)) for k in ("blocked", "spa", "jsonld", "regex", "meta"))}
    print("classify:", res); assert res == {"blocked": "反爬拦截", "spa": "需JS渲染", "jsonld": "解析成功", "regex": "解析成功", "meta": "解析成功"}
