"""v6 界面：设置与集成里的插件、调度队列、死信面板；/api/v1 下界面无控制台报错。"""
import os, sys, time, tempfile, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8972", "http://127.0.0.1:8971"
SHOT = os.path.join(ROOT, "screenshots", "v6"); os.makedirs(SHOT, exist_ok=True)
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
@pytest.fixture(scope="module")
def env():
    e = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db"), PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_BACKOFF_S="0.05"); e["PRICEHUB_EMB_CACHE"] = e["PRICEHUB_DB"] + ".npz"
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8971"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8972"], cwd=ROOT, env=e)]
    wait(MOCK + "/docs"); wait(API + "/healthz")
    c = httpx.Client(base_url=API, timeout=120)
    c.post("/api/v1/sources", json={"name": "演示", "type": "mock", "config": {"platform": "taobao.com", "seed": 1}}); c.post("/api/v1/sources/1/fetch")
    bad = c.post("/api/v1/sources", json={"name": "坏源", "type": "jsonfeed", "config": {"url": MOCK + "/feed.json?fail=1"}}).json()["id"]
    c.post("/api/v1/scheduler/submit", json={"source_id": bad, "wait": True, "max_attempts": 2})
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 1000}); errs = []
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text)); pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, c, errs; b.close()
    for x in ps: x.terminate()
def test_admin_platform_panel(env):
    pg, c, errs = env; pg.click("[data-tb='设置与集成']"); pg.wait_for_timeout(1200)
    v = pg.locator("[data-v6-platform]"); expect(v.locator("[data-plugin-row]", has_text="jsonfeed")).to_have_count(1)
    expect(v.locator("[data-dead-row]")).to_have_count(1); expect(v.locator("[data-dead-row]").first).to_contain_text("503")
    expect(v.locator("[data-sched-stats]")).to_contain_text("faiss")
    pg.screenshot(path=os.path.join(SHOT, "01_插件与采集队列.png"), full_page=True)
    v.locator("[data-dead-retry]").first.click(); pg.wait_for_timeout(1500)
    assert c.get("/api/v1/scheduler").json()["counters"]["submitted"] >= 2
    v.locator("[data-plugin-reload]").click(); pg.wait_for_timeout(800)
    assert not errs, errs
def test_metrics_and_docs_pages(env):
    pg, c, errs = env
    assert "pricehub_http_requests_total" in c.get("/metrics").text and c.get("/docs").status_code == 200
    for tb in ["仪表盘", "商品监控", "告警规则与通知"]: pg.click(f"[data-tb='{tb}']"); pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(SHOT, "02_仪表盘.png")); assert not errs, errs
