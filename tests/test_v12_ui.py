"""v12 界面：去重建议复核队列（匹配度与理由）、合并 + 撤销、批量、自动合并设置、合并记录、商品详情里的商品页列表。"""
import os, sys, time, httpx, subprocess, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _wait, _env
from test_v12_dedup import _mk
API = "http://127.0.0.1:8989"; SHOT = os.path.join(ROOT, "screenshots", "v12"); os.makedirs(SHOT, exist_ok=True)
@pytest.fixture(scope="module")
def env():
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8989"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory"))
    try:
        _wait(API + "/healthz"); c = httpx.Client(base_url=API, timeout=180)
        ids = {"a": _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "u41", [25900, 25500]),
               "b": _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "u42", [25900]),
               "c": _mk(c, "Apple 蘋果 【A++級福利品】iPhone 17e 256G", "24h.pchome.com.tw", "u43", [21900]),
               "d": _mk(c, "iPhone 16 Pro 256GB", "taobao.com", "u1", [7999]), "e": _mk(c, "iPhone 16 Pro 256GB", "jd.com", "u6", [8099]),
               "f": _mk(c, "Switch OLED 游戏机", "taobao.com", "u4", [2199]), "g": _mk(c, "Nintendo Switch OLED 本体", "amazon.co.jp", "u17", [2299])}
        c.post("/api/dedup/scan", json={})
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, c, ids, errs; b.close()
    finally: p.terminate()
def test_dedup_ui(env):
    pg, c, ids, errs = env
    pg.evaluate("() => Alpine.$data(document.body).openMod('dedup')")
    v = pg.locator("[data-v12-dedup]")
    card = v.locator(f"[data-dd-cand='{ids['a']}-{ids['b']}']"); expect(card).to_be_visible(timeout=30000)
    expect(card.locator("[data-dd-reasons]")).to_contain_text("不同商品页"); expect(card.locator("[data-dd-score]")).to_contain_text("匹配度")
    assert v.locator(f"[data-dd-cand='{ids['a']}-{ids['c']}']").count() == 0          # 福利品与全新不进队列
    pg.screenshot(path=os.path.join(SHOT, "01_去重建议_复核队列.png"))
    card.locator("[data-dd-merge]").click()
    expect(v.locator(f"[data-dd-cand='{ids['a']}-{ids['b']}']")).to_have_count(0, timeout=15000)
    assert ids["b"] not in {p["id"] for p in c.get("/api/products").json()}
    # 商品详情：商品页列表
    pg.evaluate(f"() => Alpine.$data(document.body).openProduct({ids['a']})"); pg.wait_for_timeout(1800)
    lst = pg.locator("[data-listings]:visible").first; expect(lst).to_contain_text("已合并 1 个")
    lst.scroll_into_view_if_needed(); pg.screenshot(path=os.path.join(SHOT, "02_商品详情_合并后的商品页.png"))
    # 合并记录 → 撤销
    pg.evaluate("() => Alpine.$data(document.body).openMod('dedup')"); pg.wait_for_timeout(500)
    v.locator("[data-dd-view-log]").click(); row = v.locator("[data-dd-merge-row]").first; expect(row).to_contain_text("生效中")
    pg.screenshot(path=os.path.join(SHOT, "03_合并记录.png"))
    row.locator("[data-dd-undo]").click(); expect(row).to_contain_text("已撤销", timeout=15000)
    assert ids["b"] in {p["id"] for p in c.get("/api/products").json()}
    # 批量：全选 → 批量标为不同
    v.locator("[data-dd-view-queue]").click(); pg.wait_for_timeout(500); v.locator("[data-dd-scope]").select_option("cross_platform"); pg.wait_for_timeout(800)
    n = v.locator("[data-dd-cand]").count(); assert n >= 1
    v.locator("[data-dd-selall]").check(); v.locator("[data-dd-batch-notsame]").click()
    expect(v.locator("[data-dd-cand]")).to_have_count(0, timeout=15000)
    # 设置
    v.locator("[data-dd-view-settings]").click(); s = v.locator("[data-dd-settings]")
    expect(s.locator("[data-dd-cross-auto]")).not_to_be_checked(); expect(s.locator("[data-dd-same-auto]")).not_to_be_checked()
    pg.screenshot(path=os.path.join(SHOT, "04_自动合并设置.png"))
    s.locator("[data-dd-same-auto]").check(); s.locator("[data-dd-save]").click(); pg.wait_for_timeout(800)
    assert c.get("/api/dedup/settings").json()["same_platform"]["auto"] is True
    assert not errs, errs
