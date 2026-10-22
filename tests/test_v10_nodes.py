"""v10：分布式采集节点（Redis Streams）——注册、心跳、任务分发、排空/恢复、节点宕机后租约到期由其他节点接管；
内存队列回退为单节点；Prophet 回测异步计算并缓存。用 2 个本地 worker 进程端到端验证。"""
import os, sys, time, signal, tempfile, subprocess, datetime, math, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, ROOT)
def _redis_ok():
    try: import redis; return redis.Redis.from_url("redis://127.0.0.1:6379/0").ping()
    except Exception: return False
def _wait(u, n=240):
    for _ in range(n):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
def _until(fn, timeout=60, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v: return v
        time.sleep(step)
    raise AssertionError("超时")
def _env(**kw):
    db = tempfile.mktemp(suffix=".db")
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    e.update(kw); return e

@pytest.fixture(scope="module")
def cluster():
    if not _redis_ok(): pytest.skip("本机没有 Redis")
    import redis; pre = f"phnodes{os.getpid()}:"; r = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    env = _env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_PREFIX=pre, PRICEHUB_EMBEDDED_WORKER="0", PRICEHUB_HEARTBEAT_S="1", PRICEHUB_LEASE_S="4")
    api = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8981"], cwd=ROOT, env=env)
    wks = []
    try:
        _wait("http://127.0.0.1:8981/healthz")   # 先让 API 建好库表，再启动 worker，避免 SQLite 初始化时互相锁库
        for _ in range(2):
            wks.append(subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "2"], cwd=ROOT, env=env)); time.sleep(3)
        c = httpx.Client(base_url="http://127.0.0.1:8981", timeout=120)
        yield c, wks, r, pre
    finally:
        api.terminate(); [w.kill() for w in wks if w.poll() is None]
        for k in r.scan_iter(pre + "*"): r.delete(k)

def _online(c): return [n for n in c.get("/api/nodes").json()["nodes"] if n["status"] in ("online", "draining")]

def test_two_nodes_register_and_share_work(cluster):
    c, wks, r, pre = cluster
    ns = _until(lambda: (lambda x: x if len(x) == 2 else None)(_online(c)), 180)
    ids = {n["id"] for n in ns}; assert {f"{n['host']}-{n['pid']}" for n in ns} == ids
    assert {w.pid for w in wks} == {n["pid"] for n in ns}
    st = c.get("/api/nodes").json(); assert st["backend"] == "redis" and st["online"] == 2 and st["lease_s"] == 4
    p = c.post("/api/nodes/probe", json={"n": 12, "sleep": 0.4, "wait": True, "timeout": 60}).json()
    nodes = [x["node"] for x in p["results"]]; assert len(nodes) == 12 and set(nodes) == ids   # 两个节点都领到任务
    # 心跳在走：last_seen 持续更新
    a = {n["id"]: n["last_seen"] for n in _online(c)}; time.sleep(2.5); b = {n["id"]: n["last_seen"] for n in _online(c)}
    assert all(b[k] > a[k] for k in a)
    done = sum(n["done"] for n in _online(c)); assert done >= 12

def test_drain_and_resume(cluster):
    c, wks, r, pre = cluster
    a, b = sorted(n["id"] for n in _online(c))
    assert c.post(f"/api/nodes/{a}/drain").json()["ok"]
    _until(lambda: any(n["id"] == a and n["status"] == "draining" for n in _online(c)), 15)
    time.sleep(1.5)
    p = c.post("/api/nodes/probe", json={"n": 6, "sleep": 0.2, "wait": True, "timeout": 60}).json()
    assert {x["node"] for x in p["results"]} == {b}            # 排空的节点不再领新任务
    assert c.post(f"/api/nodes/{a}/resume").json()["ok"]
    _until(lambda: all(n["status"] == "online" for n in _online(c)), 15)

def test_crashed_node_tasks_are_reclaimed(cluster):
    c, wks, r, pre = cluster
    p = c.post("/api/nodes/probe", json={"n": 4, "sleep": 3, "wait": False, "tag": "crash"}).json()
    jids = [j["id"] for j in p["jobs"]]
    def running(): 
        js = [r.hgetall(f"{pre}job:{i}") for i in jids]; ws = {j.get(b"worker", b"").decode() for j in js if j.get(b"state") == b"running"}
        return ws if len(ws) == 2 else None
    ws = _until(running, 30, 0.1)
    victim = next(w for w in wks if any(x.endswith(f"-{w.pid}") for x in ws)); vid = next(x for x in ws if x.endswith(f"-{victim.pid}"))
    victim.send_signal(signal.SIGKILL); victim.wait()
    def all_done():
        js = [r.hgetall(f"{pre}job:{i}") for i in jids]; return js if all(j.get(b"state") == b"done" for j in js) else None
    js = _until(all_done, 60)
    survivor = next(n["id"] for n in _online(c))
    reclaimed = [j for j in js if j.get(b"reclaimed_from", b"").decode() == vid]
    assert reclaimed and all(json_node(j) == survivor for j in reclaimed)
    # 宕机节点心跳过期后显示离线，可以移除
    ns = _until(lambda: (lambda d: d if d.get(vid) == "offline" else None)({n["id"]: n["status"] for n in c.get("/api/nodes").json()["nodes"]}), 30)
    assert c.get("/api/scheduler").json()["counters"].get("reclaimed", 0) >= 1
    assert c.post(f"/api/nodes/{vid}/forget").json()["ok"]
    assert vid not in {n["id"] for n in c.get("/api/nodes").json()["nodes"]}
def json_node(j):
    import json; return json.loads(j[b"result"])["node"]

@pytest.fixture(scope="module")
def mem():
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8982"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory"))
    _wait("http://127.0.0.1:8982/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8982", timeout=120); yield c; p.terminate()

def test_memory_fallback_single_node(mem):
    st = mem.get("/api/nodes").json(); assert st["backend"] == "memory" and len(st["nodes"]) == 1 and st["nodes"][0]["embedded"] and st["hint"]
    p = mem.post("/api/nodes/probe", json={"n": 3, "sleep": 0.1, "wait": True}).json(); assert len({x["node"] for x in p["results"]}) == 1
    assert mem.post(f"/api/nodes/{st['nodes'][0]['id']}/drain").status_code == 400

def test_prophet_backtest_async_and_cached(mem):
    from app.domains.analysis import forecast_v2 as F
    if not F.HAVE_PROPHET: pytest.skip("未安装 Prophet")
    pid = mem.post("/api/products", json={"title": "Prophet 回测商品", "platform": "test", "external_id": "pb1"}).json()["id"]
    d0 = datetime.datetime.utcnow() - datetime.timedelta(days=50)
    for i in range(50):
        mem.post(f"/api/products/{pid}/prices", json={"price": round(200 - 0.3 * i + 6 * math.sin(i * 2 * math.pi / 7), 2), "ts": (d0 + datetime.timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "method": "manual"})
    t0 = time.time(); r = mem.get(f"/api/products/{pid}/backtest").json(); fast = time.time() - t0
    assert r["ok"] and r["prophet"]["status"] in ("running", "done") and "prophet" not in r["engines"] or r["prophet"]["status"] == "done"
    assert fast < 15
    r = _until(lambda: (lambda x: x if x["prophet"]["status"] != "running" else None)(mem.get(f"/api/products/{pid}/backtest").json()), 180, 1)
    assert r["prophet"]["status"] == "done" and "prophet" in r["engines"] and r["engines"]["prophet"]["mape"] >= 0
    t0 = time.time(); r2 = mem.get(f"/api/products/{pid}/backtest").json(); assert r2["prophet"]["status"] == "done" and time.time() - t0 < 15   # 命中缓存
    # 新价格到来 → 缓存失效，重新后台计算
    mem.post(f"/api/products/{pid}/prices", json={"price": 180, "method": "manual"})
    assert mem.get(f"/api/products/{pid}/backtest").json()["prophet"]["status"] in ("running", "done")
