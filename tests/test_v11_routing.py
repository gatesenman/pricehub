"""v11：按地域/标签定向派发（Redis Streams，多个本地 worker 进程端到端）+ 回退规则 + 平台规则 + 内存队列单元测试。"""
import os, sys, time, subprocess, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _redis_ok, _wait, _until, _env

def test_match_unit():
    from app.core import routing as R
    a = {"region": "cn", "tags": ["playwright"]}
    assert R.match(a, None) and R.match(a, {"region": "cn"}) and R.match(a, {"region": ["us", "cn"]})
    assert not R.match(a, {"region": "us"}) and not R.match(a, {"tags": ["residential-proxy"]})
    assert R.match(a, {"region": "*", "tags": ["PlayWright"]}) and R.normalize({"region": "", "tags": []}) is None
    assert R.normalize({"region": "cn", "fallback": "bogus"})["fallback"] == "any"
    assert "地域 cn" in R.describe({"region": "cn", "fallback": "wait"}) and "等待" in R.describe({"region": "cn", "fallback": "wait"})

@pytest.fixture(scope="module")
def cluster():
    if not _redis_ok(): pytest.skip("本机没有 Redis")
    import redis; pre = f"phroute{os.getpid()}:"; r = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    env = _env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_PREFIX=pre, PRICEHUB_EMBEDDED_WORKER="0", PRICEHUB_HEARTBEAT_S="1", PRICEHUB_LEASE_S="5")
    api = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8984"], cwd=ROOT, env=env); wks = {}
    try:
        _wait("http://127.0.0.1:8984/healthz")
        for name, reg, tags in (("cn", "cn", "playwright"), ("us", "us", "residential-proxy")):
            wks[name] = subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "2", "--region", reg, "--tags", tags], cwd=ROOT, env=env); time.sleep(3)
        c = httpx.Client(base_url="http://127.0.0.1:8984", timeout=120)
        _until(lambda: c.get("/api/nodes").json()["online"] == 2, 180)
        yield c, wks, env, r, pre
    finally:
        api.terminate(); [w.kill() for w in wks.values() if w.poll() is None]
        for k in r.scan_iter(pre + "*"): r.delete(k)

def _nid(c, region):
    return next(n["id"] for n in c.get("/api/nodes").json()["nodes"] if n.get("region") == region and n["status"] == "online")

def test_nodes_show_labels(cluster):
    c, *_ = cluster
    ns = {n["region"]: n for n in c.get("/api/nodes").json()["nodes"]}
    assert ns["cn"]["tags"] == ["playwright"] and ns["us"]["tags"] == ["residential-proxy"]

def test_targeted_by_region_and_tag(cluster):
    c, *_ = cluster
    cn, us = _nid(c, "cn"), _nid(c, "us")
    p = c.post("/api/nodes/probe", json={"n": 8, "sleep": 0.1, "wait": True, "require": {"region": "cn"}}).json()
    assert {x["node"] for x in p["results"]} == {cn} and all(j["route"] == "matched" for j in p["jobs"])
    p = c.post("/api/nodes/probe", json={"n": 8, "sleep": 0.1, "wait": True, "require": {"tags": ["residential-proxy"]}}).json()
    assert {x["node"] for x in p["results"]} == {us}
    p = c.post("/api/nodes/probe", json={"n": 4, "sleep": 0.1, "wait": True, "require": {"region": ["us", "cn"], "tags": ["playwright"]}}).json()
    assert {x["node"] for x in p["results"]} == {cn}
    assert c.post("/api/routing/preview", json={"region": "us"}).json()["matched"] == [us]

def test_fallback_rules(cluster):
    c, wks, env, r, pre = cluster
    p = c.post("/api/nodes/probe", json={"n": 4, "sleep": 0.1, "wait": True, "require": {"region": "jp", "fallback": "any"}}).json()
    assert all(j["state"] == "done" and j["route"] == "fallback" for j in p["jobs"])
    p = c.post("/api/nodes/probe", json={"n": 2, "sleep": 0.1, "wait": True, "timeout": 30, "require": {"region": "jp", "fallback": "fail"}}).json()
    assert all(j["state"] == "dead" and "没有满足要求" in j["error"] for j in p["jobs"])
    # wait：先排队，等日本节点上线后由它执行
    p = c.post("/api/nodes/probe", json={"n": 3, "sleep": 0.1, "wait": False, "tag": "jpwait", "require": {"region": "jp", "fallback": "wait"}}).json()
    ids = [j["id"] for j in p["jobs"]]; time.sleep(3)
    assert all(r.hget(f"{pre}job:{i}", "state") == b"waiting_node" for i in ids)
    wks["jp"] = subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "1", "--region", "jp"], cwd=ROOT, env=env)
    _until(lambda: all(r.hget(f"{pre}job:{i}", "state") == b"done" for i in ids), 180)
    jp = _nid(c, "jp"); assert all(r.hget(f"{pre}job:{i}", "worker").decode() == jp for i in ids)

def test_platform_rule_for_source(cluster):
    c, *_ = cluster
    us = _nid(c, "us")
    rules = c.put("/api/routing/rules", json={"rules": [{"match": "jd.com", "region": ["us"], "fallback": "wait"}, {"match": "", "region": "x"}]}).json()["rules"]
    assert len(rules) == 1 and "地域 us" in rules[0]["desc"]
    sid = c.post("/api/sources", json={"name": "京东mock-路由", "type": "mock", "config": {"platform": "jd.com", "seed": 5}}).json()["id"]
    j = c.post("/api/scheduler/submit", json={"source_id": sid, "wait": True, "timeout": 90}).json()
    assert j["state"] == "done" and j["worker"] == us and j["route"] == "matched" and j["payload"]["_require"]["region"] == ["us"]
    # 数据源自带 require 优先于规则
    sid2 = c.post("/api/sources", json={"name": "京东mock-cn", "type": "mock", "config": {"platform": "jd.com", "seed": 6, "require": {"region": "cn"}}}).json()["id"]
    j = c.post("/api/scheduler/submit", json={"source_id": sid2, "wait": True, "timeout": 90}).json()
    assert j["state"] == "done" and j["worker"] == _nid(c, "cn")

@pytest.fixture(scope="module")
def mem():
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8985"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory", PRICEHUB_NODE_REGION="cn", PRICEHUB_NODE_TAGS="playwright"))
    _wait("http://127.0.0.1:8985/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8985", timeout=120); yield c; p.terminate()

def test_memory_backend_routing(mem):
    n = mem.get("/api/nodes").json()["nodes"][0]; assert n["region"] == "cn" and n["tags"] == ["playwright"]
    p = mem.post("/api/nodes/probe", json={"n": 2, "sleep": 0.05, "wait": True, "require": {"region": "cn"}}).json()
    assert all(j["route"] == "matched" and j["state"] == "done" for j in p["jobs"])
    p = mem.post("/api/nodes/probe", json={"n": 2, "sleep": 0.05, "wait": True, "require": {"region": "us"}}).json()
    assert all(j["route"] == "fallback" and j["state"] == "done" for j in p["jobs"])
    p = mem.post("/api/nodes/probe", json={"n": 1, "sleep": 0.05, "wait": True, "timeout": 10, "require": {"region": "us", "fallback": "fail"}}).json()
    assert p["jobs"][0]["state"] == "dead"
