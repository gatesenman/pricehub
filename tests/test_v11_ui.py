"""v11 界面：采集节点显示地域/标签、按要求派发探测、派发规则编辑保存。"""
import os, sys, time, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _redis_ok, _wait, _env, _until
API = "http://127.0.0.1:8987"; SHOT = os.path.join(ROOT, "screenshots", "v11"); os.makedirs(SHOT, exist_ok=True)
@pytest.fixture(scope="module")
def env():
    if not _redis_ok(): pytest.skip("本机没有 Redis")
    import redis; pre = f"phui11{os.getpid()}:"; r = redis.Redis.from_url("redis://127.0.0.1:6379/0")
    e = _env(PRICEHUB_QUEUE="redis", PRICEHUB_REDIS_PREFIX=pre, PRICEHUB_EMBEDDED_WORKER="0", PRICEHUB_HEARTBEAT_S="1")
    api = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8987"], cwd=ROOT, env=e); wks = []
    try:
        _wait(API + "/healthz")
        for reg, tags in (("cn", "playwright"), ("us", "residential-proxy,playwright")):
            wks.append(subprocess.Popen([PY, "-m", "app.worker", "--concurrency", "2", "--region", reg, "--tags", tags], cwd=ROOT, env=e)); time.sleep(3)
        c = httpx.Client(base_url=API, timeout=60); _until(lambda: c.get("/api/nodes").json()["online"] == 2, 180)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, c, errs; b.close()
    finally:
        api.terminate(); [w.terminate() for w in wks]
        for k in r.scan_iter(pre + "*"): r.delete(k)
def test_routing_ui(env):
    pg, c, errs = env
    pg.evaluate("() => Alpine.$data(document.body).openMod('nodes')")
    v = pg.locator("[data-v10-nodes]"); expect(v.locator("[data-nodes-table] tbody tr")).to_have_count(2, timeout=30000)
    expect(v.locator("[data-node-labels]").first).to_contain_text("playwright")
    v.locator("[data-pq-tags]").fill("residential-proxy"); v.locator("[data-pq-run]").click()
    expect(v.locator("[data-pq-msg]")).to_contain_text("×6", timeout=60000)
    us = next(n["id"] for n in c.get("/api/nodes").json()["nodes"] if n["region"] == "us")
    expect(v.locator("[data-pq-msg]")).to_contain_text(us)
    pg.evaluate("() => document.querySelectorAll('[data-v10-nodes] *').forEach(e => { if (e.scrollLeft) e.scrollLeft = 0; })"); pg.mouse.click(5, 300); pg.wait_for_timeout(200)
    pg.screenshot(path=os.path.join(SHOT, "01_按标签定向派发.png"))
    v.locator("[data-rule-add]").click(); row = v.locator("[data-rule-row]").last
    row.locator("[data-rule-match]").fill("jd.com"); row.locator("[data-rule-region]").fill("cn"); row.locator("[data-rule-tags]").fill("playwright")
    row.locator("select").select_option("wait"); v.locator("[data-rule-save]").click()
    expect(row).to_contain_text("地域 cn", timeout=10000)
    assert c.get("/api/routing/rules").json()["rules"][0]["fallback"] == "wait"
    pg.evaluate("() => document.querySelectorAll('[data-v10-nodes] *').forEach(e => { if (e.scrollLeft) e.scrollLeft = 0; })"); pg.mouse.click(5, 300); pg.wait_for_timeout(200)
    pg.screenshot(path=os.path.join(SHOT, "02_派发规则.png"))
    v.locator("[data-pq-region]").fill("jp"); v.locator("[data-pq-tags]").fill(""); v.locator("[data-pq-run]").click()
    expect(v.locator("[data-pq-msg]")).to_contain_text("地域 jp", timeout=60000)
    pg.evaluate("() => document.querySelectorAll('[data-v10-nodes] *').forEach(e => { if (e.scrollLeft) e.scrollLeft = 0; })"); pg.mouse.click(5, 300); pg.wait_for_timeout(200)
    pg.screenshot(path=os.path.join(SHOT, "03_无匹配回退.png"))
    assert not errs, errs
