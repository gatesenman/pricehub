"""v9：预测回测（滚动起点）与官方适配器重试/熔断（仅用本地 mock，不连第三方）。"""
import os, sys, time, datetime, subprocess, tempfile, httpx, pytest, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, ROOT)

def _hist(n=70, seed=1):
    r = np.random.default_rng(seed); d0 = datetime.date.today() - datetime.timedelta(days=n)
    return [{"price": round(100 + 0.1 * i + 4 * np.sin(i * 2 * np.pi / 7) + r.normal(0, 0.5), 2),
             "ts": (d0 + datetime.timedelta(days=i)).isoformat()} for i in range(n)]

def test_backtest_unit():
    from app.domains.analysis.backtest import backtest
    r = backtest(_hist(), horizon=7, folds=4)
    assert r["ok"] and r["folds"] == 4 and r["best"] in r["engines"]
    assert {"naive", "baseline", "stl+arima"} <= set(r["engines"])
    for v in r["engines"].values(): assert v["mape"] >= 0 and v["mae"] >= 0
    # 有周季节性的序列上，季节性引擎应优于“维持现价”
    assert r["engines"]["stl+arima"]["mape"] < r["engines"]["naive"]["mape"]
    assert r["skill_vs_naive_pct"] > 0 and r["reliability"] in ("高", "中", "低")
    cov = r["engines"]["stl+arima"]["ci90_coverage"]; assert cov is not None and 60 <= cov <= 100   # 区间校准后覆盖率应接近或高于 90%
    short = backtest(_hist(10), horizon=7)
    assert not short["ok"] and "不足" in short["reason"]

def _wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)

@pytest.fixture(scope="module")
def mock():
    p = subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_official_api:app", "--port", "8993"], cwd=ROOT, env=dict(os.environ, PYTHONPATH=ROOT))
    _wait("http://127.0.0.1:8993/docs"); yield "http://127.0.0.1:8993"; p.terminate()

def _ad(base, **cfg):
    from app.domains.official import adapter as A
    A.BREAKER.reset()
    ad = A.make("ebay_browse" if "ebay_browse" in A.REGISTRY else list(A.REGISTRY)[1], mock_base_url=base, config=cfg); return ad, A

def test_retry_then_success(mock):
    httpx.post(mock + "/flaky/reset"); ad, A = _ad(mock, max_retries=3, backoff_base=0.05, rate_limit_rps=100)
    r = ad.request("GET", "/flaky/search", params={"fail": 2, "code": 503})
    assert r.status_code == 200 and len(r.json()["items"]) == 5
    retries = [a for a in ad.audit_log() if a.get("retry")]; assert len(retries) == 2
    assert all(abs(a["sleep"] - 0.1) < 1e-6 for a in retries)   # 遵守 Retry-After
    assert A.BREAKER.state(ad.meta.id)["fails"] == 0

def test_circuit_breaker_opens_and_resets(mock):
    httpx.post(mock + "/flaky/reset"); ad, A = _ad(mock, max_retries=1, backoff_base=0.01, breaker_threshold=3, breaker_cooldown=30, rate_limit_rps=100)
    from app.sdk import RetryableError, PluginError
    with pytest.raises(RetryableError): ad.request("GET", "/flaky/search", params={"fail": 99, "code": 429})
    with pytest.raises(RetryableError): ad.request("GET", "/flaky/search", params={"fail": 99, "code": 429})
    st = A.BREAKER.state(ad.meta.id); assert st["open"] and st["fails"] >= 3
    n_before = len(ad.audit_log())
    with pytest.raises(RetryableError, match="熔断"): ad.request("GET", "/flaky/search")
    assert len(ad.audit_log()) == n_before   # 熔断期间不再发请求
    A.BREAKER.reset(ad.meta.id); httpx.post(mock + "/flaky/reset")
    assert ad.request("GET", "/flaky/search", params={"fail": 0}).status_code == 200
    # 4xx 不重试
    with pytest.raises(PluginError): ad.request("GET", "/flaky/search", params={"fail": 99, "code": 404})

@pytest.fixture(scope="module")
def api():
    db = tempfile.mktemp(suffix=".db")
    env = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8997"], cwd=ROOT, env=env)
    _wait("http://127.0.0.1:8997/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8997", timeout=120)
    yield c; p.terminate()

def test_backtest_and_health_api(api):
    c = api
    pid = c.post("/api/products", json={"title": "回测测试商品", "platform": "test", "external_id": "bt1"}).json()["id"]
    for h in _hist(40): c.post(f"/api/products/{pid}/prices", json={"price": h["price"], "ts": h["ts"] + " 08:00:00", "method": "manual"})
    r = c.get(f"/api/products/{pid}/backtest?horizon=7&folds=3").json()
    assert r.get("ok") and r["folds"] == 3, r
    assert True and r["best"]
    pid2 = c.post("/api/products", json={"title": "短历史", "platform": "test", "external_id": "bt2"}).json()["id"]
    assert c.get(f"/api/products/{pid2}/backtest").json()["ok"] is False
    h = c.get("/api/official/health").json(); assert h and all("open" in v for v in h.values())
    k = next(iter(h)); assert c.post(f"/api/official/adapters/{k}/reset_breaker").json()["ok"]
