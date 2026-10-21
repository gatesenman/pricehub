"""v5 界面 e2e：各新模块能打开、可操作、无控制台报错；通知只发往本地模拟接收端。"""
import os, sys, time, tempfile, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8942", "http://127.0.0.1:8941"
SHOT = os.path.join(ROOT, "screenshots", "v5"); os.makedirs(SHOT, exist_ok=True)
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def env():
    e = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db"), PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1"); e["PRICEHUB_EMB_CACHE"] = e["PRICEHUB_DB"] + ".npz"
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8941"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8942"], cwd=ROOT, env=e)]
    wait(MOCK + "/docs"); wait(API + "/api/health")
    c = httpx.Client(base_url=API, timeout=120)
    c.post("/api/sources", json={"name": "演示", "type": "mock", "config": {"platform": "taobao.com", "seed": 1}}); c.post("/api/sources/1/fetch")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox","--disable-dev-shm-usage"]); pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text)); pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(API + "/"); pg.wait_for_timeout(1500)
        yield pg, c, errs
        b.close()
    for x in ps: x.terminate()
def tb(pg, name): pg.click(f"[data-tb='{name}']"); pg.wait_for_timeout(800)
def test_rules_channels(env):
    pg, c, errs = env; httpx.delete(MOCK + "/hooks"); tb(pg, "告警规则与通知")
    pg.locator("[data-v5-rules] select").first.select_option("feishu")
    pg.locator("[data-v5-rules] input[placeholder*='Webhook']").fill(MOCK + "/hooks/feishu-ui")
    pg.click("[data-nc-add]"); pg.wait_for_timeout(800)
    assert c.get("/api/channels").json()["items"][0]["url"].startswith(MOCK)
    pg.locator("[data-v5-rules] button:has-text('测试')").first.click(); pg.wait_for_timeout(1500)
    assert httpx.get(MOCK + "/hooks/feishu-ui").json()
    pg.locator("[data-v5-rules] input[placeholder='价格']").fill("5000"); pg.click("[data-nr-add]"); pg.wait_for_timeout(600); assert c.get("/api/rules").json()
    pg.screenshot(path=os.path.join(SHOT, "01_告警规则与通知.png")); assert not errs, errs
def test_map_reports_admin(env):
    pg, c, errs = env
    for i, n in enumerate(["控价与卖家", "报表与日历", "设置与集成"], 2):
        tb(pg, n); pg.screenshot(path=os.path.join(SHOT, f"0{i}_{n}.png"))
    assert pg.locator("[data-fx-save]").count() and pg.locator("[data-backup]").count(); assert not errs, errs
def test_match_and_pricing(env):
    pg, c, errs = env
    pg.click("[data-menu=工具]"); pg.locator("[data-mi='同款匹配']:visible").first.click(); pg.wait_for_timeout(2000)
    pg.screenshot(path=os.path.join(SHOT, "05_同款匹配.png"))
    pg.click("[data-menu=工具]"); pg.locator("[data-mi='定价模型']:visible").first.click(); pg.wait_for_timeout(800)
    assert pg.locator("[data-pricing-engine]").count(); pg.screenshot(path=os.path.join(SHOT, "06_定价引擎.png")); assert not errs, errs
def test_product_landed_advice(env):
    pg, c, errs = env; tb(pg, "商品监控"); pg.locator("[data-row]").first.dblclick(); pg.wait_for_timeout(2000)
    assert pg.locator("[data-landed]").count() and pg.locator("[data-verdict]").count()
    pg.screenshot(path=os.path.join(SHOT, "07_商品到手价与买入建议.png")); assert not errs, errs
