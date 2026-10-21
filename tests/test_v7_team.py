"""v7 团队协作测试：免登录桌面模式、本地账号、团队模式、工作区隔离、角色权限、邀请、用量配额（SKU/采集/API 调用）、
OIDC 单点登录（本地模拟提供方）、Redis Streams 分布式队列 + 独立 worker 进程、无 Redis 时回退内存队列。全部只连本地服务。"""
import os, sys, time, tempfile, subprocess, urllib.parse, httpx, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK, IDP = "http://127.0.0.1:8974", "http://127.0.0.1:8973", "http://127.0.0.1:8971"
def _wait(u, n=180):
    for _ in range(n):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError("服务未启动：" + u)
def _env(**kw):
    db = tempfile.mktemp(suffix=".db")
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_BACKOFF_S="0.05", PRICEHUB_DOMAIN_RPS="50", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    e.update(kw); return e
@pytest.fixture(scope="module")
def srv():
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8973"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_oidc:app", "--port", "8971"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8974"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory"))]
    for u in (MOCK + "/docs", IDP + "/jwks", API + "/healthz"): _wait(u)
    yield
    for p in ps: p.terminate()
def cl(): return httpx.Client(base_url=API, timeout=60)
def test_team_flow(srv):
    anon = cl()
    me = anon.get("/api/auth/me").json(); assert me["need_login"] is False and me["workspace"]["id"] == 1   # 单机免登录
    assert anon.post("/api/products", json={"title": "默认工作区商品"}).status_code == 200
    own = cl(); assert own.post("/api/auth/register", json={"email": "owner@example.com", "name": "老板", "password": "secret123"}).status_code == 200
    me = own.get("/api/auth/me").json(); assert me["logged_in"] and me["role"] == "owner" and me["workspace"]["id"] == 1
    assert own.post("/api/auth/mode", json={"enabled": True}).json()["team_mode"]
    r = anon.get("/api/products"); assert r.status_code == 401 and r.json()["need_login"]                   # 团队模式需登录
    assert anon.post("/api/auth/register", json={"email": "x@example.com", "password": "secret123"}).status_code == 403
    bad = cl(); assert bad.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"}).status_code == 401
    # 新工作区隔离
    ws2 = own.post("/api/workspaces", json={"name": "二号店", "plan": "free"}).json()["id"]
    assert own.get("/api/products").json() == []
    own.post("/api/products", json={"title": "二号店商品"})
    own.post("/api/auth/switch", json={"ws_id": 1}); t1 = {p["title"] for p in own.get("/api/products").json()}
    assert "默认工作区商品" in t1 and "二号店商品" not in t1
    own.post("/api/auth/switch", json={"ws_id": ws2}); assert [p["title"] for p in own.get("/api/products").json()] == ["二号店商品"]
    # 邀请 + 角色
    inv = own.post("/api/workspace/invites", json={"email": "viewer@example.com", "role": "viewer"}).json()
    tok = urllib.parse.parse_qs(urllib.parse.urlparse(inv["invite_path"]).query)["invite"][0]
    assert cl().get("/api/invites/preview", params={"token": tok}).json()["role"] == "viewer"
    v = cl(); assert v.post("/api/auth/register", json={"email": "viewer@example.com", "password": "secret123", "invite": tok}).status_code == 200
    v.post("/api/auth/login", json={"email": "viewer@example.com", "password": "secret123"})
    me = v.get("/api/auth/me").json(); assert me["workspace"]["id"] == ws2 and me["role"] == "viewer"
    assert v.get("/api/products").status_code == 200 and v.post("/api/products", json={"title": "不允许"}).status_code == 403
    assert v.post("/api/auth/switch", json={"ws_id": 1}).status_code in (403, 404)                     # 不是默认工作区成员
    m = own.get("/api/workspace/members").json(); vid = [x for x in m["items"] if x["email"] == "viewer@example.com"][0]["id"]
    assert own.put(f"/api/workspace/members/{vid}", json={"role": "editor"}).status_code == 200
    assert v.post("/api/products", json={"title": "编辑可写"}).status_code == 200
    assert v.post("/api/workspace/invites", json={"email": "z@example.com"}).status_code == 403            # 编辑不能管理成员
    # 配额：SKU / 采集 / API 调用
    assert own.put("/api/workspace/settings", json={"quota": {"max_skus": 2, "collects_per_month": 1, "api_calls_per_day": 100000}}).status_code == 200
    r = own.post("/api/products", json={"title": "第三个"}); assert r.status_code == 429 and "上限" in r.json()["detail"] or "配额" in r.json()["detail"]
    sid = own.post("/api/sources", json={"name": "feed", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json"}}).json()["id"]
    own.put("/api/workspace/settings", json={"quota": {"max_skus": 100, "collects_per_month": 1, "api_calls_per_day": 100000}})
    assert own.post(f"/api/sources/{sid}/fetch").status_code == 200
    time.sleep(6)   # 计量每 5 秒落库
    assert own.post(f"/api/sources/{sid}/fetch").status_code == 429
    j = own.post("/api/scheduler/submit", json={"source_id": sid, "wait": True}).json(); assert j["state"] == "dead" and "配额" in j["error"] or "上限" in j["error"]
    used = {u["metric"]: u["used"] for u in own.get("/api/usage").json()["items"]}; assert used["collects"] >= 1 and used["api_calls"] >= 5 and used["skus"] >= 3
    calls = used["api_calls"]
    own.put("/api/workspace/settings", json={"quota": {"max_skus": 100, "collects_per_month": 10, "api_calls_per_day": calls + 1}}); time.sleep(6)
    codes = [own.get("/api/products").status_code for _ in range(5)]; assert 429 in codes
    own.put("/api/workspace/settings", json={"quota": {"api_calls_per_day": None, "max_skus": None, "collects_per_month": None}})
    # OIDC 单点登录（本地模拟提供方）
    own.post("/api/auth/switch", json={"ws_id": 1})
    assert own.post("/api/auth/oidc/config", json={"issuer": IDP, "client_id": "pricehub", "client_secret": "mock-secret", "name": "模拟 SSO"}).status_code == 200
    inv = own.post("/api/workspace/invites", json={"email": "sso.user@example.com", "role": "editor"}).json()
    tok = urllib.parse.parse_qs(urllib.parse.urlparse(inv["invite_path"]).query)["invite"][0]
    sso = httpx.Client(base_url=API, timeout=60, follow_redirects=True)
    r = sso.get("/api/auth/oidc/login", params={"invite": tok, "login_hint": "sso.user@example.com"}); assert r.status_code == 200 and "login_error" not in str(r.url)
    me = sso.get("/api/auth/me").json(); assert me["logged_in"] and me["user"]["email"] == "sso.user@example.com" and me["role"] == "editor"
    r = httpx.Client(base_url=API, follow_redirects=False).get("/api/auth/oidc/callback", params={"code": "x", "state": "forged"}); assert "login_error" in r.headers["location"]
    # 关闭团队模式后恢复免登录
    assert own.post("/api/auth/mode", json={"enabled": False}).status_code == 200 and anon.get("/api/products").status_code == 200
def _redis_ok():
    try: import redis; return redis.Redis.from_url("redis://127.0.0.1:6379/0").ping()
    except Exception: return False
@pytest.mark.skipif(not _redis_ok(), reason="本机没有 Redis")
def test_redis_queue_with_separate_worker(srv):
    import redis; pre = f"phtest{os.getpid()}:"; r = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    env = _env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_PREFIX=pre, PRICEHUB_EMBEDDED_WORKER="0")
    api = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8975"], cwd=ROOT, env=env)
    wk = subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "2"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait("http://127.0.0.1:8975/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8975", timeout=120)
        st = c.get("/api/v1/scheduler").json(); assert st["backend"] == "redis" and st["running"] is False   # API 进程不消费
        sid = c.post("/api/v1/sources", json={"name": "feed", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json"}}).json()["id"]
        j = c.post("/api/v1/scheduler/submit", json={"source_id": sid, "wait": True, "timeout": 90}).json()
        assert j["state"] == "done" and j["result"]["fetched"] == 2 and j["backend"] == "redis"
        assert "Feed 测试耳机 Pro" in {p["title"] for p in c.get("/api/v1/products").json()}
        bad = c.post("/api/v1/sources", json={"name": "bad", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json?fail=1"}}).json()["id"]
        j = c.post("/api/v1/scheduler/submit", json={"source_id": bad, "wait": True, "max_attempts": 3, "timeout": 60}).json()
        assert j["state"] == "dead" and j["attempts"] == 3 and "503" in j["error"]
        assert c.get("/api/v1/deadletters").json()[0]["attempts"] == 3
        st = c.get("/api/v1/scheduler").json(); assert st["counters"]["done"] >= 1 and st["counters"]["retried"] >= 2 and st["consumers"]
        rec = [x for x in st["recent"] if x["id"] == j["id"]][0]; assert rec["state"] == "dead"
    finally:
        api.terminate(); wk.terminate()
        for k in r.scan_iter(pre + "*"): r.delete(k)
def test_redis_unavailable_falls_back_to_memory(srv):
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8976"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_URL="redis://127.0.0.1:6390/0"))
    try:
        _wait("http://127.0.0.1:8976/healthz"); c = httpx.Client(base_url="http://127.0.0.1:8976", timeout=60)
        assert c.get("/api/v1/scheduler").json()["backend"] == "memory"
        sid = c.post("/api/v1/sources", json={"name": "feed", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json"}}).json()["id"]
        assert c.post("/api/v1/scheduler/submit", json={"source_id": sid, "wait": True}).json()["state"] == "done"
    finally: p.terminate()
