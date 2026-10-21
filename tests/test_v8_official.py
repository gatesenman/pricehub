"""v8 官方接口适配器：目录、加密凭证、mock 测连与同步；渲染池状态；扩展包下载；pgvector 状态说明。"""
import os, sys, time, tempfile, subprocess, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8994", "http://127.0.0.1:8991"
def _wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def env():
    db = tempfile.mktemp(suffix=".db")
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1",
             PRICEHUB_SECRET="test-secret-v8", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_official_api:app", "--port", "8991"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8994"], cwd=ROOT, env=e)]
    _wait(MOCK + "/paapi5/search?q=x"); _wait(API + "/healthz")
    yield httpx.Client(base_url=API, timeout=60)
    for p in ps: p.terminate()
def test_catalog_and_crypto_roundtrip(env):
    from app.domains.official import crypto
    os.environ["PRICEHUB_SECRET"] = "test-secret-v8"
    tok = crypto.seal("super-secret-key"); assert tok.startswith("enc:") and crypto.open_(tok) == "super-secret-key"
    assert "•" in crypto.mask(tok) and "super" not in crypto.mask(tok)
    cat = env.get("/api/official/adapters").json()
    ids = {x["id"] for x in cat["items"]}
    for need in ("amazon_paapi5", "ebay_browse", "aliexpress_affiliate", "jd_union", "taobao_open", "keepa", "serpapi_google_shopping"):
        assert need in ids
    tb = next(x for x in cat["items"] if x["id"] == "taobao_open"); assert tb["status"] == "stub"
def test_mock_test_and_sync(env):
    c = env
    r = c.put("/api/official/adapters/amazon_paapi5", json={"enabled": True, "mock_base_url": MOCK,
        "credentials": {"access_key": "AKIA_TEST", "secret_key": "SECRET", "partner_tag": "tag-20"}}).json()
    amz = next(x for x in r["items"] if x["id"] == "amazon_paapi5")
    assert amz["enabled"] and amz["has_credentials"] and "•" in amz["credentials_masked"]["access_key"]
    t = c.post("/api/official/adapters/amazon_paapi5/test", json={}).json()
    assert t["ok"] and t["count"] >= 1 and t["sample"][0]["title"]
    s = c.post("/api/official/adapters/amazon_paapi5/sync", json={"max_pages": 2}).json()
    assert s["ok"] and s["fetched"] >= 5 and s["product_ids"]
    titles = {p["title"] for p in c.get("/api/products").json()}; assert any("测试商品" in t for t in titles)
    # stub 无 mock 应失败
    c.put("/api/official/adapters/taobao_open", json={"enabled": True, "mock_base_url": "", "credentials": {"app_key": "k", "app_secret": "s"}})
    bad = c.post("/api/official/adapters/taobao_open/sync", json={"force": True})
    assert bad.status_code == 400 and "占位" in bad.json()["detail"]
    # 有 mock 的 stub 可跑
    ok = c.post("/api/official/adapters/taobao_open/test", json={"mock_base_url": MOCK}).json(); assert ok["ok"]
def test_render_extension_pgvector(env):
    st = env.get("/api/render/status").json()
    assert "workers" in st and "queue_max" in st
    col = env.post("/api/collect/settings", json={"render_workers": 2, "render_queue": 16, "render_fallback": True, "proxies": []}).json()
    assert col["render_workers"] == 2 and col["extension_url"].endswith("pricehub-clipper.zip")
    z = env.get("/downloads/pricehub-clipper.zip"); assert z.status_code == 200 and z.headers["content-type"].startswith("application/zip") and len(z.content) > 100
    pv = env.get("/api/pgvector/status").json()
    assert pv["ok"] is False and ("未开启" in pv.get("reason", "") or "PostgreSQL" in pv.get("reason", "") or pv.get("enabled_flag") is False)
