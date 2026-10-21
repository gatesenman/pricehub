"""v4 体验优化 e2e：首次引导、全局搜索建议、粘贴链接一键监控、后台任务中心、批量操作与撤销、布局记忆、虚拟滚动、平台不可用说明、无控制台报错。"""
import os, re, sys, time, sqlite3, subprocess, tempfile, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8922", "http://127.0.0.1:8921"; SHOTS = os.path.join(ROOT, "screenshots", "v4"); DB = tempfile.mktemp(suffix=".db")
EMOJI_RE = r"[\u2190-\u21FF\u2300-\u23FF\u25A0-\u27BF\u2B00-\u2BFF\uFF0B\U0001F000-\U0001FAFF]"
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def page():
    env = dict(os.environ, PRICEHUB_DB=DB, PRICEHUB_EMB_CACHE=DB + ".npz"); env.pop("DEEPSEEK_API_KEY", None)
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8921"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8922"], cwd=ROOT, env=env)]
    wait(MOCK + "/docs"); wait(API + "/api/health"); os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(); ctx = b.new_context(viewport={"width": 1440, "height": 900}); pg = ctx.new_page(); pg.errors = []
        pg.on("console", lambda m: m.type in ("error", "warning") and pg.errors.append(m.type + ": " + m.text)); pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        yield pg
        b.close()
    for p in ps: p.terminate()
C = lambda: httpx.Client(base_url=API, timeout=60)
def shot(pg, name): pg.wait_for_timeout(350); pg.screenshot(path=os.path.join(SHOTS, name))
V = lambda pg, t: pg.locator(f"[data-view={t}]:visible")

def test_api_v4_basics(page):
    c = C()
    assert c.get("/api/settings").json()["onboarded"] is False
    s = c.post("/api/settings", json={"ai_provider": "kimi", "ai_key": "sk-abcdefgh12345678"}).json()
    assert s["ai_keys"]["kimi"]["set"] and "••••" in s["ai_keys"]["kimi"]["masked"] and "abcdefgh1234" not in str(s)
    assert c.get("/api/ai/providers").json()["kimi"]["configured"]            # 立即生效，无需重启
    c.post("/api/settings", json={"ai_provider": "kimi", "ai_key": ""}); assert not c.get("/api/ai/providers").json()["kimi"]["configured"]
    r = c.get("/api/suggest", params={"q": "https://item.jd.com/100012043978.html"}).json()
    assert r["url"]["platform"]["name"].startswith("JD") and r["url"]["platform"]["explain"]["next"]
    assert c.post("/api/quick-monitor", json={"url": "not a url"}).status_code == 400
    assert c.post("/api/tasks", json={"kind": "nope"}).status_code == 400
    assert c.post("/api/undo/expired").status_code == 404
    x = c.get("/api/platforms/1/explain").json(); assert x["title"] and x["next"]
    assert len(c.get("/api/onboarding/countries").json()) > 50

def test_onboarding_wizard(page):
    page.goto(API + "/"); ob = page.locator("[data-onboarding]"); expect(ob).to_be_visible(); shot(page, "01-onboarding-countries.png")
    ob.locator("[data-ob-country='日本']").click(); expect(ob.locator("[data-ob-country='日本']")).to_have_class(re.compile("on"))
    ob.locator("[data-ob-next]").click(); expect(ob.locator("[data-ob-step='1']")).to_be_visible()
    ob.locator("[data-ob-key]").fill("sk-test-key-0123456789"); expect(ob.locator("[data-ob-next]")).to_have_text("保存并继续"); shot(page, "02-onboarding-ai.png")
    ob.locator("[data-ob-next]").click(); expect(ob.locator("[data-ob-step='2']")).to_be_visible()
    ob.locator("[data-ob-url]").fill(MOCK + "/shop/jsonld"); ob.locator("[data-ob-monitor]").click()
    expect(ob.locator("[data-ob-result]")).to_contain_text("Sony", timeout=20000)
    ob.locator("[data-ob-demo]").click(); expect(ob.locator("[data-ob-demo]")).to_contain_text("已导入演示数据", timeout=20000); shot(page, "03-onboarding-first-product.png")
    ob.locator("[data-ob-next]").click(); expect(ob.locator("[data-ob-step='3']")).to_contain_text("日本"); ob.locator("[data-ob-next]").click()
    expect(ob).to_be_hidden(); expect(page.locator("[data-status-platforms]")).to_contain_text("已启用 6/920")
    s = C().get("/api/settings").json(); assert s["onboarded"] and s["countries"] == ["日本"] and s["ai_keys"][s["ai_provider"]]["set"]
    C().post("/api/settings", json={"ai_provider": s["ai_provider"], "ai_key": ""})
    expect(V(page, "dashboard").locator("[data-stat]").first).not_to_have_text("0"); shot(page, "04-main-ui.png")
    page.reload(); expect(page.locator("[data-onboarding]")).to_be_hidden()      # 只在首次出现

def test_global_search_suggest(page):
    q = page.locator("[data-quick]"); q.fill("Sony"); sug = page.locator("[data-suggest]")
    expect(sug.locator("[data-sug=product]").first).to_contain_text("Sony"); expect(sug.locator("[data-sug=semantic]")).to_be_visible(); shot(page, "05-search-suggest.png")
    q.press("Enter"); expect(V(page, "product").locator("[data-product-title]")).to_contain_text("Sony")
    q.fill("Rakuten"); expect(sug.locator("[data-sug=platform]").first).to_contain_text("Rakuten"); q.press("Escape"); expect(sug).to_be_hidden()
    q.fill(MOCK + "/shop/meta"); expect(sug.locator("[data-sug=monitor]")).to_contain_text("一键监控")
    q.press("Enter"); expect(page.locator("[data-qm-result]")).to_contain_text("AirPods", timeout=20000); page.keyboard.press("Escape")

def test_paste_link_one_click_monitor(page):
    page.locator("[data-tab]").first.click()
    page.evaluate("""u => { const dt = new DataTransfer(); dt.setData('text', u); window.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt})); }""", MOCK + "/shop/microdata")
    qm = page.locator("[data-qm]"); expect(qm).to_be_visible(); expect(qm.locator("[data-qm-result]")).to_contain_text("Kindle", timeout=20000)
    expect(qm.locator("[data-qm-result]")).to_contain_text("1,068.00"); shot(page, "06-one-click-monitor.png")
    expect(page.locator("[data-toast-action]", has_text="查看商品").last).to_be_visible()
    qm.locator("[data-qm-open]").click(); expect(V(page, "product").locator("[data-product-title]")).to_contain_text("Kindle")
    assert any(s["name"].startswith("一键监控") for s in C().get("/api/sources").json())       # 已自动加入定时监控

def test_unavailable_link_explained(page):
    page.keyboard.press("Control+m"); qm = page.locator("[data-qm]"); expect(qm).to_be_visible()
    qm.locator("[data-qm-url]").fill(MOCK + "/shop/blocked"); qm.locator("[data-qm-go]").click()
    ex = qm.locator("[data-qm-explain]"); expect(ex).to_contain_text("没能从这个页面读到价格", timeout=20000)
    expect(ex).to_contain_text("拦截"); expect(ex).to_contain_text("可以这样做"); shot(page, "07-unavailable-explain.png")
    qm.locator("[data-qm-url]").fill(MOCK + "/shop/spa"); qm.locator("[data-qm-go]").click(); expect(ex).to_contain_text("浏览器", timeout=20000)
    page.keyboard.press("Escape"); expect(qm).to_be_hidden()

def test_platform_unavailable_in_catalog(page):
    sql = "UPDATE platforms SET status='反爬拦截', status_detail='HTTP 403 验证/拦截页' WHERE domain='amazon.co.jp'"
    if os.environ.get("PRICEHUB_DB_URL"):   # PostgreSQL 后端：通过同一存储层（同一 schema）直接改库
        os.environ["PRICEHUB_DB"] = DB; from app.core import storage; storage.PG_SCHEMA[0] = None; storage.DB_PATH = DB; storage._PG.c = None; storage.ex(sql)
    else:
        con = sqlite3.connect(DB); con.execute(sql); con.commit(); con.close()
    page.reload(); page.click("[data-tb='平台目录']"); v = V(page, "catalog"); v.locator("[data-cat-q]").fill("amazon.co.jp")
    row = v.locator("[data-cat-row]").first; expect(row).to_contain_text("拦截自动访问"); row.click()
    ex = page.locator("[data-explain]"); expect(ex).to_contain_text("网站拦截了自动访问"); expect(ex).to_contain_text("建议下一步")
    if "已启用" in row.locator("[data-enable]").inner_text(): row.locator("[data-enable]").click(); expect(row.locator("[data-enable]")).to_contain_text("启用")
    row.locator("[data-enable]").click(); d = page.locator("[data-dialog]"); expect(d).to_contain_text("启用前请注意"); expect(d).to_contain_text("开放接口")
    shot(page, "08-platform-unavailable.png"); page.locator("[data-dlg-ok]").click(); expect(row.locator("[data-enable]")).to_contain_text("已启用")

def test_catalog_virtual_scroll(page):
    v = V(page, "catalog"); v.locator("[data-cat-q]").fill(""); expect(v.locator("[data-cat-total]")).to_have_text("920")
    n = v.locator("[data-cat-row]").count(); assert 20 < n < 90, n          # 920 行只渲染可视区域
    last = C().get("/api/platforms", params={"limit": 5000}).json()["items"][-1]["name"]
    v.locator("[data-cat-scroll]").evaluate("e => e.scrollTop = e.scrollHeight"); expect(v.locator("[data-cat-row]").last).to_contain_text(last)
    assert v.locator("[data-cat-row]").count() < 90

def test_background_tasks(page):
    page.click("[data-menu=数据源]"); page.click("[data-mi='立即抓取全部']")
    expect(page.locator("[data-toast]", has_text="后台").last).to_be_visible()          # 不阻塞，立即反馈
    expect(page.locator("[data-toast]", has_text="监控抓取完成").last).to_be_visible(timeout=30000)
    page.click("[data-task-center]"); p = page.locator("[data-task-panel]"); expect(p).to_be_visible()
    expect(p.locator("[data-task-item]").first).to_contain_text("已完成"); assert p.locator("[data-task-item]").count() >= 3; shot(page, "09-background-tasks.png")
    page.keyboard.press("Escape"); expect(p).to_be_hidden()

def test_batch_ops_and_undo(page):
    page.click("[data-tb='商品监控']"); v = V(page, "products"); total = int(v.locator("[data-total]").inner_text())
    v.locator("[data-row]").nth(0).locator("[data-check]").check(); v.locator("[data-row]").nth(1).locator("[data-check]").check()
    b = v.locator("[data-batch]"); expect(b).to_be_visible(); expect(b.locator("[data-batch-count]")).to_have_text("2"); shot(page, "10-batch-bar.png")
    b.locator("[data-batch-unmonitor]").click(); expect(page.locator("[data-toast]", has_text="已暂停监控 2").last).to_be_visible()
    b.locator("[data-batch-delete]").click(); expect(v.locator("[data-total]")).to_have_text(str(total - 2)); expect(b).to_be_hidden()
    expect(page.locator("[data-toast-action]", has_text="撤销").last).to_be_visible(); shot(page, "11-undo-toast.png")
    page.locator("body").click(position={"x": 700, "y": 880}); page.keyboard.press("Control+z"); expect(v.locator("[data-total]")).to_have_text(str(total))

def test_layout_and_filters_remembered(page):
    v = V(page, "products"); v.locator("[data-filter-q]").fill("Sony"); expect(v.locator("[data-row]")).to_have_count(1)
    side = page.locator("[data-side]"); s = page.locator("[data-split-side]").bounding_box(); w0 = side.bounding_box()["width"]
    page.mouse.move(s["x"] + 2, 300); page.mouse.down(); page.mouse.move(s["x"] + 62, 300, steps=4); page.mouse.up()
    assert abs(side.bounding_box()["width"] - (w0 + 60)) < 6, (w0, side.bounding_box())
    th = v.locator("th[data-sort=title]"); h = th.locator("[data-resize]").bounding_box(); tw = th.bounding_box()["width"]
    page.mouse.move(h["x"] + 2, h["y"] + 5); page.mouse.down(); page.mouse.move(h["x"] + 62, h["y"] + 5, steps=4); page.mouse.up()
    page.wait_for_timeout(1200); page.reload()
    v = V(page, "products"); expect(v).to_be_visible(); expect(v.locator("[data-filter-q]")).to_have_value("Sony"); expect(v.locator("[data-row]")).to_have_count(1)
    page.wait_for_function(f"Math.abs(document.querySelector('[data-side]').offsetWidth - {w0 + 60}) < 6", timeout=5000)
    assert v.locator("th[data-sort=title]").bounding_box()["width"] > tw + 50
    v.locator("[data-clear-filter]").click(); expect(v.locator("[data-filter-q]")).to_have_value("")

def test_skeleton_and_empty_states(page):
    page.route("**/api/products/*/history", lambda r: (time.sleep(1.2), r.continue_()))
    V(page, "products").locator("[data-row]").first.dblclick(); expect(V(page, "product").locator("[data-skeleton]")).to_be_visible()
    expect(V(page, "product").locator("[data-product-title]")).to_be_visible(timeout=10000); page.unroute("**/api/products/*/history")
    page.click("[data-tb='告警']"); expect(V(page, "alerts")).to_be_visible()

def test_no_emoji_no_console_errors(page):
    txt = page.evaluate("document.body.innerText + [...document.querySelectorAll('[title],[placeholder]')].map(e=>(e.title||'')+(e.placeholder||'')).join(' ')")
    assert not re.findall(EMOJI_RE, txt); assert page.errors == [], page.errors
