"""v9 界面：商品详情“回测预测准确度”按钮与结果表；无控制台报错。"""
import os, sys, time, tempfile, subprocess, datetime, math, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API = "http://127.0.0.1:8998"
SHOT = os.path.join(ROOT, "screenshots", "v9"); os.makedirs(SHOT, exist_ok=True)
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
@pytest.fixture(scope="module")
def env():
    db = tempfile.mktemp(suffix=".db")
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db+".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    srv = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8998"], cwd=ROOT, env=e)
    wait(API + "/healthz"); c = httpx.Client(base_url=API, timeout=120)
    pid = c.post("/api/products", json={"title": "v9 回测演示耳机", "platform": "demo", "external_id": "v9-1"}).json()["id"]
    pid2 = c.post("/api/products", json={"title": "v9 新上架商品", "platform": "demo", "external_id": "v9-2"}).json()["id"]
    base = datetime.datetime.utcnow() - datetime.timedelta(days=60)
    for i in range(60):
        c.post(f"/api/products/{pid}/prices", json={"price": round(300 - i * 0.6 + 8 * math.sin(i * 2 * math.pi / 7), 2),
            "ts": (base + datetime.timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "method": "manual"})
    for i in range(5):
        c.post(f"/api/products/{pid2}/prices", json={"price": 99 + i, "ts": (base + datetime.timedelta(days=55 + i)).strftime("%Y-%m-%d %H:%M:%S"), "method": "manual"})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []
        pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, pid, pid2, errs; b.close()
    srv.terminate()
def _open(pg, pid):
    pg.evaluate(f"() => {{ const a = Alpine.$data(document.body); a.openProduct({pid}); }}"); pg.wait_for_timeout(1500)
def test_backtest_ui(env):
    pg, pid, pid2, errs = env
    _open(pg, pid)
    box = pg.locator("[data-backtest]:visible").first; expect(box).to_be_visible()
    box.locator("[data-backtest-run]").click()
    expect(box.locator("[data-backtest-table]")).to_be_visible(timeout=60000)
    assert box.locator("[data-backtest-table] tbody tr").count() >= 3
    expect(box.locator("[data-backtest-note]")).to_contain_text("平均误差")
    box.scroll_into_view_if_needed(); pg.screenshot(path=os.path.join(SHOT, "01_预测回测.png"))
    box.screenshot(path=os.path.join(SHOT, "02_预测回测_结果表.png"))
    # 短历史商品：给出原因而不是空白
    _open(pg, pid2)
    box2 = pg.locator("[data-backtest]:visible")
    if box2.count():
        box2.first.locator("[data-backtest-run]").click()
        expect(box2.first.locator("[data-backtest-reason]")).to_contain_text("不足", timeout=30000)
    assert not errs, errs
