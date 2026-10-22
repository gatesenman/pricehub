"""v13 界面：重新扫描后显示"分块候选 / 全比较"对数；设置页的分块开关、k、单块上限可保存；新采集商品走增量检测。"""
import os, sys, httpx, subprocess, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_v10_nodes import _wait, _env
from test_v12_dedup import _mk
API = "http://127.0.0.1:8987"; SHOT = os.path.join(ROOT, "screenshots", "v13"); os.makedirs(SHOT, exist_ok=True)
@pytest.fixture(scope="module")
def env():
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8987"], cwd=ROOT, env=_env(PRICEHUB_QUEUE="memory"))
    try:
        _wait(API + "/healthz"); c = httpx.Client(base_url=API, timeout=180)
        ids = {"a": _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "w41", [25900]), "b": _mk(c, "Apple 蘋果 iPhone 17e (256G)", "24h.pchome.com.tw", "w42", [25900]),
               "d": _mk(c, "iPhone 16 Pro 256GB", "taobao.com", "w1", [7999]), "e": _mk(c, "iPhone 16 Pro 256GB", "jd.com", "w6", [8099]),
               "f": _mk(c, "Switch OLED 游戏机", "taobao.com", "w4", [2199]), "g": _mk(c, "Dyson 戴森 吹风机 HD15", "jd.com", "w9", [2999])}
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 1440, "height": 900}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(API + "/"); pg.wait_for_timeout(1500); yield pg, c, ids, errs; b.close()
    finally: p.terminate()
def test_blocking_ui(env):
    pg, c, ids, errs = env
    r = c.post("/api/dedup/scan", json={}).json()
    assert r["mode"] == "blocking" and r["pairs"] <= r["all_pairs"] and r["candidates"] >= 2 and "total" in r["blocking"]
    pg.evaluate("() => Alpine.$data(document.body).openMod('dedup')"); v = pg.locator("[data-v12-dedup]")
    v.locator("[data-dd-scan]").click(); ls = v.locator("[data-dd-lastscan]"); expect(ls).to_contain_text("分块候选", timeout=30000); expect(ls).to_contain_text("全比较")
    expect(v.locator(f"[data-dd-cand='{ids['a']}-{ids['b']}']")).to_be_visible()
    pg.screenshot(path=os.path.join(SHOT, "01_去重扫描_分块候选统计.png"))
    v.locator("[data-dd-view-settings]").click(); s = v.locator("[data-dd-settings]")
    expect(s.locator("[data-dd-block-on]")).to_be_checked(); s.locator("[data-dd-block-k]").fill("40")
    pg.screenshot(path=os.path.join(SHOT, "02_去重设置_分块参数.png"))
    s.locator("[data-dd-save]").click(); pg.wait_for_timeout(800)
    st = c.get("/api/dedup/settings").json()["blocking"]; assert st["ann_k"] == 40 and st["enabled"] is True
    # 关闭分块 → 回退全比较，结果一致
    c.put("/api/dedup/settings", json={"blocking": {"enabled": False}}); r2 = c.post("/api/dedup/scan", json={}).json()
    assert r2["mode"] == "all_pairs" and r2["candidates"] == r["candidates"]
    c.put("/api/dedup/settings", json={"blocking": {"enabled": True}})
    # 新商品：增量检测只查新商品
    n = _mk(c, "Apple iPhone 16 Pro 256GB 公司貨", "momoshop.com.tw", "w7", [8199])
    r3 = c.post("/api/dedup/scan", json={"ids": [n]}).json()
    assert r3["mode"] == "blocking" and r3["all_pairs"] == 6 and r3["pairs"] <= 6 and r3["candidates"] >= 1
    assert not errs, errs
