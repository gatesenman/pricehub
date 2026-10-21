"""v6 平台化测试：调度器（优先级/限速/退避/死信）、插件 SDK 动态加载、事件总线链路（采集→告警→通知，仅本地模拟端点）、
/api/v1 版本前缀与旧路径兼容、/metrics、健康检查、FAISS 向量索引。"""
import os, sys, time, tempfile, subprocess, threading, textwrap, httpx, pytest, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8962", "http://127.0.0.1:8961"
MH = {"Authorization": "Bearer metrics-test-token"}
sys.path.insert(0, ROOT)
# ---------- 单元：调度器 ----------
def mk_sched(**kw):
    from app.core.scheduler import Scheduler
    return Scheduler(**kw)
def test_scheduler_priority_and_rate_limit():
    s = mk_sched(workers=1, rate=1000, burst=1000); order = []; gate = threading.Event()
    s.register_handler("block", lambda p: gate.wait(5)); s.register_handler("rec", lambda p: order.append(p["n"]))
    b = s.submit("block", {}, key="k0")
    time.sleep(0.05); js = [s.submit("rec", {"n": n}, key="k1", priority=pr) for n, pr in [(1, 9), (2, 0), (3, 5)]]
    gate.set(); assert s.wait([b] + js, 5); assert order == [2, 3, 1]
    s2 = mk_sched(workers=4, rate=5, burst=1); t = []
    s2.register_handler("t", lambda p: t.append(time.monotonic()))
    jobs = [s2.submit("t", {}, key="slow.example") for _ in range(4)]; assert s2.wait(jobs, 10)
    t.sort(); gaps = [b - a for a, b in zip(t, t[1:])]; assert min(gaps) > 0.15, gaps   # 5 次/秒 → 间隔约 0.2 秒
    assert s2.stats()["counters"]["throttled"] >= 3
def test_scheduler_backoff_retry_and_dead_letter():
    from app.core.scheduler import PermanentError
    dead = []; s = mk_sched(workers=2, rate=1000, burst=1000, base_backoff=0.05, dead_sink=dead.append); n = {"c": 0}
    def flaky(p):
        n["c"] += 1
        if n["c"] < 3: raise TimeoutError("超时")
        return "ok"
    s.register_handler("flaky", flaky); s.register_handler("bad", lambda p: (_ for _ in ()).throw(ConnectionError("拒绝连接")))
    s.register_handler("perm", lambda p: (_ for _ in ()).throw(PermanentError("配置错误")))
    a = s.submit("flaky", {}); b = s.submit("bad", {}, max_attempts=3); c = s.submit("perm", {}, max_attempts=5)
    assert s.wait([a, b, c], 10)
    assert a.state == "done" and a.attempts == 3 and a.result == "ok"
    assert b.state == "dead" and b.attempts == 3 and c.state == "dead" and c.attempts == 1
    assert {j.id for j in dead} == {b.id, c.id}
    assert 0.04 <= s.backoff(1) <= 0.06 and 0.16 <= s.backoff(3) <= 0.24
def test_event_bus_isolation_and_wildcard():
    from app.core.events import EventBus
    bus = EventBus(); got = []
    bus.subscribe("price.*", lambda t, **k: got.append(("w", t))); bus.subscribe("price.recorded", lambda t, **k: 1 / 0, "boom"); bus.subscribe("*", lambda t, **k: got.append(("all", t)))
    assert bus.publish("price.recorded", pid=1) == 2 and bus.errors["boom"] == 1 and ("w", "price.recorded") in got and ("all", "price.recorded") in got
def test_faiss_vector_index_matches_bruteforce():
    from app.core.vector import VectorIndex, HAVE_FAISS
    assert HAVE_FAISS
    rng = np.random.default_rng(0); X = rng.normal(size=(5000, 64)).astype("float32"); X /= np.linalg.norm(X, axis=1, keepdims=True)
    q = X[123] + 0.01; q /= np.linalg.norm(q)
    bf = np.argsort(-(X @ q))[:5]
    vi = VectorIndex(kind="flat").build(X); I, D = vi.search(q, 5); assert vi.engine == "faiss-flat" and list(I) == list(bf)
    a = VectorIndex().build(X); I3, _ = a.search(q, 5); assert a.engine == "numpy-exact" and list(I3) == list(bf)   # 小规模自动走精确检索
    h = VectorIndex(kind="hnsw").build(X); I2, _ = h.search(q, 5); assert h.engine == "faiss-hnsw" and I2[0] == 123
# ---------- 集成：服务 ----------
@pytest.fixture(scope="module")
def c():
    pdir = tempfile.mkdtemp(); import shutil
    shutil.copytree(os.path.join(ROOT, "plugins", "jsonfeed_example"), os.path.join(pdir, "jsonfeed_example"))
    open(os.path.join(pdir, "broken.py"), "w").write("import not_a_module_xyz\n")
    env = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db"), PRICEHUB_NO_CLIP="1", PRICEHUB_PLUGINS_DIR=pdir, PRICEHUB_BACKOFF_S="0.05", PRICEHUB_DOMAIN_RPS="50",
               PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT, PRICEHUB_METRICS_STRICT="1", PRICEHUB_METRICS_TOKEN="metrics-test-token")
    env["PRICEHUB_EMB_CACHE"] = env["PRICEHUB_DB"] + ".npz"
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8961"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8962"], cwd=ROOT, env=env)]
    for u in (MOCK + "/docs", API + "/healthz"):
        for _ in range(180):
            try:
                if httpx.get(u, timeout=2).status_code < 500: break
            except Exception: pass
            time.sleep(1)
    cl = httpx.Client(base_url=API, timeout=120); cl.pdir = pdir; yield cl
    for p in ps: p.terminate()
def test_api_v1_prefix_and_legacy(c):
    a, b = c.get("/api/v1/health"), c.get("/api/health")
    assert a.status_code == b.status_code == 200 and a.json()["ok"] and a.headers["x-api-version"] == "v1"
    assert "x-api-legacy-path" in b.headers and "x-api-legacy-path" not in a.headers
    assert c.get("/api/v1/products").status_code == 200
def test_health_and_metrics(c):
    assert c.get("/healthz").json()["status"] == "ok"
    r = c.get("/readyz").json(); assert r["status"] == "ready" and r["checks"]["database"]["ok"] and r["checks"]["vector_index"]["engine"].startswith("faiss")
    m = c.get("/metrics", headers=MH).text
    for k in ("pricehub_http_requests_total", "pricehub_products", "pricehub_job_queue_depth", "pricehub_up 1.0"): assert k in m
def test_plugin_sdk_dynamic_load(c):
    cat = c.get("/api/v1/plugins").json(); ids = {p["id"]: p for p in cat["plugins"]}
    assert ids["jsonfeed"]["origin"].endswith("plugin.py") and ids["jsonfeed"]["state"] == "active" and ids["mock"]["origin"] == "builtin"
    assert any(r["state"] == "error" and "broken.py" in r["file"] for r in cat["last_load"])
    t = c.post("/api/v1/plugins/jsonfeed/test", json={"config": {"url": MOCK + "/feed.json"}}).json()
    assert t["ok"] and t["count"] == 2 and t["sample"][0]["price"] == 1299.0 and t["sample"][0]["platform"] == "jsonfeed"
    assert not c.post("/api/v1/plugins/jsonfeed/test", json={"config": {}}).json()["ok"]
    open(os.path.join(c.pdir, "extra.py"), "w").write(textwrap.dedent('''
        from app.sdk import SourcePlugin, PluginMeta
        class Extra(SourcePlugin):
            meta = PluginMeta(id="extra_demo", name="热加载示例")
            def fetch_items(self, ctx): return [{"external_id": "x1", "title": "热加载商品", "price": 9.9}]
    '''))
    r = c.post("/api/v1/plugins/reload").json(); assert "extra_demo" in {p["id"] for p in r["plugins"]}
def test_scheduler_api_event_chain_and_dead_letter(c):
    httpx.delete(MOCK + "/hooks")
    ch = c.post("/api/v1/channels", json={"name": "erp", "type": "webhook", "url": MOCK + "/hooks/v6", "config": {"events": ["price_change"]}}).json()["id"]
    c.post("/api/v1/rules", json={"type": "any", "channel_ids": [ch], "cooldown_min": 0})
    sid = c.post("/api/v1/sources", json={"name": "feed", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json"}}).json()["id"]
    j = c.post("/api/v1/scheduler/submit", json={"source_id": sid, "wait": True}).json()
    assert j["state"] == "done" and j["result"]["fetched"] == 2 and j["key"] == "127.0.0.1:8961"
    titles = {p["title"] for p in c.get("/api/v1/products").json()}; assert "Feed 测试耳机 Pro" in titles
    ev = c.get("/api/v1/events").json()["published"]
    assert ev.get("collect.succeeded", 0) >= 1 and ev.get("price.recorded", 0) >= 2 and ev.get("notify.sent", 0) >= 1
    assert httpx.get(MOCK + "/hooks/v6").json()     # 采集 → 事件总线 → 规则 → 本地模拟 Webhook
    bad = c.post("/api/v1/sources", json={"name": "feed-bad", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json?fail=1"}}).json()["id"]
    j = c.post("/api/v1/scheduler/submit", json={"source_id": bad, "wait": True, "max_attempts": 3}).json()
    assert j["state"] == "dead" and j["attempts"] == 3 and "503" in j["error"]
    dl = c.get("/api/v1/deadletters").json(); assert dl and dl[0]["attempts"] == 3
    assert c.post(f"/api/v1/deadletters/{dl[0]['id']}/retry").json()["kind"] == "source.fetch"
    st = c.get("/api/v1/scheduler").json(); assert st["counters"]["retried"] >= 2 and st["counters"]["dead"] >= 1
    m = c.get("/metrics", headers=MH).text; assert 'pricehub_collect_total{method="jsonfeed",result="ok"}' in m and "pricehub_jobs_total" in m
    assert c.post("/api/v1/monitor/run").json()["ok"]

def test_metrics_requires_token(c):
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/metrics", headers=MH).status_code == 200
    tok = c.post("/api/v1/tokens", json={"name": "监控", "role": "viewer"}).json()["token"]
    assert c.get("/metrics", headers={"X-API-Key": tok}).status_code == 200
