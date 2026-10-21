"""v8 侧栏导航：活动栏切换/折叠与记忆、工作台分组覆盖全部模块、商品筛选标签、平台目录单维度+国家搜索、浅色主题。无控制台报错。截图 screenshots/v8_nav/。"""
import os, sys, time, tempfile, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable; API = "http://127.0.0.1:8998"
SHOT = os.path.join(ROOT, "screenshots", "v8_nav"); os.makedirs(SHOT, exist_ok=True)
@pytest.fixture(scope="module")
def pg():
    db = tempfile.mktemp(suffix=".db"); e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1")
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8998"], cwd=ROOT, env=e)
    for _ in range(180):
        try:
            if httpx.get(API + "/healthz", timeout=2).status_code == 200: break
        except Exception: pass
        time.sleep(1)
    c = httpx.Client(base_url=API, timeout=120)
    for i, plat in enumerate(["taobao.com", "jd.com"]): c.post("/api/sources", json={"name": f"演示{i}", "type": "mock", "config": {"platform": plat, "seed": i + 1}}); c.post(f"/api/sources/{i + 1}/fetch")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"]); page = b.new_page(viewport={"width": 1440, "height": 900}); page.errs = []
        page.on("console", lambda m: m.type == "error" and page.errs.append(m.text)); page.on("pageerror", lambda x: page.errs.append(str(x)))
        page.goto(API + "/"); page.evaluate("localStorage.clear()"); page.reload(); page.wait_for_timeout(1500); yield page; b.close()
    p.terminate()
def test_workbench_groups_cover_all_modules(pg):
    expect(pg.locator("[data-side][data-nav-view=work]")).to_be_visible()
    for g, t in [("w_mon", "监控"), ("w_ana", "分析"), ("w_dec", "决策"), ("w_srch", "搜索"), ("w_data", "数据")]: expect(pg.locator(f"[data-group-node={g}]")).to_contain_text(t)
    mods = pg.evaluate("Object.values(MODS).map(m=>m[1])"); work = pg.locator("[data-side] [data-node]").all_inner_texts()
    pg.click("[data-act=settings]"); sett = pg.locator("[data-side] [data-node]").all_inner_texts(); pg.click("[data-act=work]")
    names = {x.strip() for x in work + sett}; assert set(mods) <= names, set(mods) - names
    pg.click("[data-node='商品监控']"); expect(pg.locator("[data-node='商品监控']")).to_have_class(__import__("re").compile("sel"))
    pg.wait_for_timeout(400); pg.screenshot(path=os.path.join(SHOT, "01_工作台.png"))
def test_goods_filter_chips(pg):
    pg.click("[data-act=goods]"); rows = pg.locator("[data-view=products]:visible [data-row]"); n = rows.count()
    pg.click("[data-node='jd.com']"); expect(pg.locator("[data-chip=platform]")).to_contain_text("来源平台：jd.com"); assert rows.count() < n
    pg.wait_for_timeout(300); pg.screenshot(path=os.path.join(SHOT, "02_商品_来源平台筛选.png"))
    pg.click("[data-chips-clear]"); expect(rows).to_have_count(n)
    rows.nth(0).locator("input[type=checkbox]").check(); pg.click("[data-fav-checked]"); pg.click("[data-node='已收藏']"); expect(rows).to_have_count(1); pg.click("[data-chip-clear=fav]")
def test_catalog_single_dimension_and_memory(pg):
    pg.click("[data-act=catalog]"); expect(pg.locator("[data-group-node]")).to_have_count(1)
    pg.click("[data-cat-dim=country]"); pg.fill("[data-nav-filter]", "日本"); pg.locator("[data-node='日本']").click()
    expect(pg.locator("[data-view=catalog]:visible [data-cat-total]")).to_have_text("6"); assert "已启用" in pg.locator("[data-node='日本'] .badge").get_attribute("title")
    pg.wait_for_timeout(300); pg.screenshot(path=os.path.join(SHOT, "03_平台目录_国家.png")); pg.fill("[data-nav-filter]", "")
    pg.click("[data-act=catalog]"); expect(pg.locator("[data-side]")).to_be_hidden(); pg.click("[data-act=catalog]")
    pg.reload(); pg.wait_for_timeout(1500); expect(pg.locator("[data-side][data-nav-view=catalog]")).to_be_visible(); expect(pg.locator("[data-cat-dim=country]")).to_have_class(__import__("re").compile("on"))
def test_light_theme(pg):
    pg.click("[data-act=work]"); pg.evaluate("Alpine.$data(document.body).toggleTheme()"); pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(SHOT, "04_浅色_工作台.png")); pg.evaluate("Alpine.$data(document.body).toggleTheme()")
    assert not pg.errs, pg.errs
