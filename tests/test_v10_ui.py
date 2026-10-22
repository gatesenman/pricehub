"""v10 界面：设置 → 采集节点（2 个 Redis worker）、派发探测任务、排空/恢复；商品详情回测含 Prophet（后台完成后自动刷新）。"""
import os, sys, time, math, datetime, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _redis_ok, _wait, _env
API = "http://127.0.0.1:8983"; SHOT = os.path.join(ROOT, "screenshots", "v10"); os.makedirs(SHOT, exist_ok=True)
@pytest.fixture(scope="module")
def env():
    if not _redis_ok(): pytest.skip("本机没有 Redis")
    import redis; pre = f"phui{os.getpid()}:"; r = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    e = _env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_PREFIX=pre, PRICEHUB_EMBEDDED_WORKER="0", PRICEHUB_HEARTBEAT_S="1", PRICEHUB_LEASE_S="5")
    api = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8983"], cwd=ROOT, env=e); wks = []
    try:
        _wait(API + "/healthz")
        for _ in range(2): wks.append(subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "2"], cwd=ROOT, env=e)); time.sleep(3)
        c = httpx.Client(base_url=API, timeout=120)
        for _ in range(180):
            if c.get("/api/nodes").json()["online"] == 2: break
            time.sleep(1)
        pid = c.post("/api/products", json={"title": "v10 Prophet 回测演示", "platform": "demo", "external_id": "v10-1"}).json()["id"]
        d0 = datetime.datetime.utcnow() - datetime.timedelta(days=60)
        for i in range(60):
            c.post(f"/api/products/{pid}/prices", json={"price": round(260 - 0.5 * i + 7 * math.sin(i * 2 * math.pi / 7) + ((i * 37) % 11 - 5) * 0.4, 2), "ts": (d0 + datetime.timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "method": "manual"})
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, pid, errs; b.close()
    finally:
        api.terminate(); [w.terminate() for w in wks]
        for k in r.scan_iter(pre + "*"): r.delete(k)
def test_nodes_page(env):
    pg, pid, errs = env
    pg.evaluate("() => Alpine.$data(document.body).openMod('nodes')")
    v = pg.locator("[data-v10-nodes]"); expect(v.locator("[data-nodes-table] tbody tr")).to_have_count(2, timeout=30000)
    expect(v.locator("[data-nodes-summary]")).to_contain_text("在线 2 个")
    v.locator("[data-nodes-probe]").click()
    expect(v.locator("[data-nodes-probe]")).to_be_enabled(timeout=60000)
    pg.wait_for_timeout(2500); pg.evaluate("() => { const a=Alpine.$data(document.body); a.loadNodes(a.tabs.find(t=>t.type==='nodes')); }"); pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(SHOT, "01_采集节点_两个节点.png"))
    v.locator("[data-node-drain]:visible").first.click()
    expect(v.locator("[data-node-status='draining']")).to_have_count(1, timeout=20000)
    pg.screenshot(path=os.path.join(SHOT, "02_采集节点_排空.png"))
    v.locator("[data-node-resume]:visible").first.click()
    expect(v.locator("[data-node-status='online']")).to_have_count(2, timeout=20000)
    # 侧栏「设置」视图里有入口
    pg.locator("[data-activity='settings'], [data-act='settings']").first.click() if pg.locator("[data-activity='settings'], [data-act='settings']").count() else None
    assert not errs, errs
def test_backtest_with_prophet(env):
    pg, pid, errs = env
    pg.evaluate(f"() => Alpine.$data(document.body).openProduct({pid})"); pg.wait_for_timeout(1500)
    box = pg.locator("[data-backtest]:visible").first; box.locator("[data-backtest-run]").click()
    expect(box.locator("[data-backtest-table]")).to_be_visible(timeout=60000)
    expect(box.locator("[data-prophet-status]")).to_contain_text("已包含 Prophet", timeout=240000)
    expect(box.locator("[data-backtest-table] tbody tr")).to_have_count(4)
    box.scroll_into_view_if_needed(); box.screenshot(path=os.path.join(SHOT, "03_回测含Prophet.png"))
    assert not errs, errs
