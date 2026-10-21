"""v8 界面：买入建议新字段、官方数据源页测连（本地 mock）、渲染池与扩展下载入口。无控制台报错。"""
import os, sys, time, tempfile, subprocess, datetime, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8996", "http://127.0.0.1:8991"
SHOT = os.path.join(ROOT, "screenshots", "v8"); os.makedirs(SHOT, exist_ok=True)
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
@pytest.fixture(scope="module")
def env():
    db = tempfile.mktemp(suffix=".db")
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db+".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1",
             PRICEHUB_SECRET="ui-secret", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_official_api:app", "--port", "8991"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8996"], cwd=ROOT, env=e)]
    wait(MOCK + "/search.json"); wait(API + "/healthz")
    c = httpx.Client(base_url=API, timeout=120)
    pid = c.post("/api/products", json={"title": "v8 预测演示耳机", "platform": "demo", "external_id": "v8-1"}).json()["id"]
    base = datetime.datetime.utcnow() - datetime.timedelta(days=50)
    for i in range(50):
        c.post(f"/api/products/{pid}/prices", json={"price": 300 - i * 0.8 + (12 if i % 7 == 0 else 0),
            "ts": (base + datetime.timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "method": "manual"})
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text)); pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, c, pid, errs; b.close()
    for x in ps: x.terminate()
def test_advice_and_official_ui(env):
    pg, c, pid, errs = env
    # 打开商品详情看买入建议
    pg.evaluate(f"window.Alpine && document.querySelector('[x-data]')"); pg.wait_for_timeout(300)
    # 通过命令面板或直接 API 触发 openProduct —— 用搜索
    pg.fill("[data-quick]", "v8 预测"); pg.wait_for_timeout(800)
    if pg.locator("[data-suggest] .sug-it").count():
        pg.locator("[data-suggest] .sug-it").first.click(); pg.wait_for_timeout(1500)
    else:
        # 退路：直接调前端
        pg.evaluate(f"() => {{ const a = Alpine.$data(document.body); a.openProduct && a.openProduct({pid}); }}")
        pg.wait_for_timeout(1500)
    adv = pg.locator("[data-advice]"); expect(adv.locator("[data-verdict]")).to_be_visible()
    expect(adv.locator("[data-buy-prob]")).to_be_visible()
    expect(adv.locator("[data-forecast-ci]")).to_be_visible()
    pg.screenshot(path=os.path.join(SHOT, "01_买入建议与预测.png"), full_page=True)
    # 官方数据源
    pg.evaluate("() => { const a=Alpine.$data(document.body); a.openMod && a.openMod('official'); }"); pg.wait_for_timeout(1000)
    v = pg.locator("[data-v8-official]"); expect(v.locator("[data-adapter='amazon_paapi5']")).to_be_visible()
    amz = v.locator("[data-adapter='amazon_paapi5']")
    amz.locator("[data-mock='amazon_paapi5']").fill(MOCK)
    amz.locator("[data-cred='amazon_paapi5.access_key']").fill("AKIA_UI")
    amz.locator("[data-cred='amazon_paapi5.secret_key']").fill("SECRET")
    amz.locator("[data-cred='amazon_paapi5.partner_tag']").fill("tag-20")
    amz.locator("[data-adapter-enable]").check(); pg.wait_for_timeout(400)
    amz.locator("[data-adapter-save]").click(); pg.wait_for_timeout(800)
    amz.locator("[data-adapter-test]").click(); pg.wait_for_timeout(1500)
    expect(amz).to_contain_text("测连成功")
    pg.screenshot(path=os.path.join(SHOT, "02_官方数据源.png"), full_page=True)
    # 设置页：渲染池与扩展
    pg.evaluate("() => { const a=Alpine.$data(document.body); a.openMod && a.openMod('admin'); }"); pg.wait_for_timeout(1200)
    r = pg.locator("[data-v8-render]"); expect(r.locator("[data-ext-download]")).to_be_visible()
    r.locator("[data-render-workers]").fill("2"); r.locator("[data-render-save]").click(); pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(SHOT, "03_渲染池与扩展.png"), full_page=True)
    assert not errs, errs
