"""v8 智能预测：季节性引擎、买入概率与置信区间、弹性粗估；API /forecast 与 /advice。"""
import os, sys, time, tempfile, subprocess, datetime, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
def test_forecast_engines_unit():
    from app.domains.analysis.forecast_v2 import forecast_advanced, HAVE_PROPHET, HAVE_SM
    assert HAVE_SM
    hist = [{"price": 100 + i * 0.2 + (6 if i % 14 < 2 else 0),
             "ts": (datetime.date.today() - datetime.timedelta(days=80 - i)).isoformat()} for i in range(80)]
    def promo(d):
        return [{"name": "双11", "date": (datetime.date.today() + datetime.timedelta(days=25)).isoformat(),
                 "days_left": 25, "warmup_days": 10, "in_progress": False}]
    r = forecast_advanced(hist, 7, promo_fn=promo)
    assert r["engine"] in ("prophet", "stl+arima", "baseline")
    assert len(r["linear_regression"]) == 7 and r["ci90"][0] <= r["point_forecast"] <= r["ci90"][1]
    assert 0.05 <= r["buy"]["probability"] <= 0.95 and len(r["buy"]["ci90"]) == 2
    assert r["elasticity"]["ok"] and r["elasticity"]["elasticity"] < 0
    # 短序列回退
    short = [{"price": 10 + i, "ts": (datetime.date.today() - datetime.timedelta(days=5 - i)).isoformat()} for i in range(5)]
    r2 = forecast_advanced(short, 3, promo_fn=promo)
    assert r2["engine"] in ("baseline", "stl+arima") and "linear_regression" in r2
def _wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def api():
    db = tempfile.mktemp(suffix=".db")
    env = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8992"], cwd=ROOT, env=env)
    _wait("http://127.0.0.1:8992/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8992", timeout=120)
    # 造一条有足够历史的商品
    pid = c.post("/api/products", json={"title": "预测测试耳机", "platform": "test", "external_id": "fc1"}).json()["id"]
    base = datetime.datetime.utcnow() - datetime.timedelta(days=60)
    for i in range(60):
        ts = (base + datetime.timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S")
        c.post(f"/api/products/{pid}/prices", json={"price": 200 - i * 0.5 + (10 if i % 7 == 0 else 0), "ts": ts, "method": "manual"})
    yield c, pid
    p.terminate()
def test_forecast_and_advice_api(api):
    c, pid = api
    f = c.get(f"/api/products/{pid}/forecast?horizon=7").json()
    assert "linear_regression" in f and f.get("engine") and f.get("buy") and f.get("ci90")
    a = c.get(f"/api/products/{pid}/advice").json()
    assert a["verdict"] in ("现在买", "可以买", "再等等", "观望")
    assert a.get("buy") and a.get("forecast_engine") and a.get("ci90")
