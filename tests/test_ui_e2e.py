"""Playwright UI e2e（桌面软件风格 v2）：覆盖菜单/工具栏/导航树/多标签/面板拖动/右键/对话框/快捷键/主题，断言无控制台报错与警告。"""
import os, re, sys, time, subprocess, tempfile, io, httpx, pytest
from PIL import Image
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK = "http://127.0.0.1:8912", "http://127.0.0.1:8911"; SHOTS = os.path.join(ROOT, "screenshots", "test")
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError(u)
@pytest.fixture(scope="module")
def page():
    env = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db")); env.pop("DEEPSEEK_API_KEY", None)
    env.pop("PRICEHUB_NO_ST", None)  # 语义搜索 e2e 需要多语言模型
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8911"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8912"], cwd=ROOT, env=env)]
    wait(MOCK + "/docs"); wait(API + "/api/health")
    # v14：模型后台预热；等语义引擎真正可用（不要把「加载中」误当成就绪）
    for _ in range(180):
        try:
            te = httpx.get(API + "/api/health", timeout=2).json().get("text_engine") or ""
            if "sentence-transformers" in te and "按需" not in te and "加载中" not in te: break
            if "tfidf" in te and "按需" not in te and "加载中" not in te:
                # 预热失败才落到 tfidf；再给一点时间后接受
                time.sleep(2); break
        except Exception: pass
        time.sleep(0.5)
    c = httpx.Client(base_url=API, timeout=60)
    for n, t, cfg in [("淘宝", "mock", {"platform": "taobao.com", "seed": 1}), ("京东", "mock", {"platform": "jd.com", "bias": 1.08, "seed": 2}),
                      ("CSV样例", "csv", {"path": ROOT + "/data/sample_products.csv"})]:
        sid = c.post("/api/sources", json={"name": n, "type": t, "config": cfg}).json()["id"]; c.post(f"/api/sources/{sid}/fetch")
    c.post("/api/settings", json={"onboarded": True})
    for i, p in enumerate([8299, 8199, 8099, 7999, 7899]): c.post("/api/products/1/prices", json={"price": p, "ts": f"2026-10-0{i+1} 12:00:00"})
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(); ctx = b.new_context(viewport={"width": 1440, "height": 900}); pg = ctx.new_page(); pg.errors = []
        pg.on("console", lambda m: m.type in ("error", "warning") and pg.errors.append(m.type + ": " + m.text)); pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        yield pg
        b.close()
    for p in ps: p.terminate()
def shot(pg, name): pg.wait_for_timeout(400); pg.screenshot(path=os.path.join(SHOTS, name))
V = lambda pg, t: pg.locator(f"[data-view={t}]:visible")
def nav(pg, view):   # 切换活动栏视图（已在该视图时不再点击，避免折叠侧栏）
    side = pg.locator("[data-side]")
    if not (side.is_visible() and side.get_attribute("data-nav-view") == view): pg.click(f"[data-act='{view}']")
    expect(pg.locator(f"[data-side][data-nav-view='{view}']")).to_be_visible()
def test_main_window(page):
    page.goto(API + "/"); expect(page.locator("[data-tab]")).to_have_count(2)
    expect(V(page, "dashboard").locator("[data-stat]").first).to_have_text("16")
    expect(page.locator("[data-status-count]")).to_have_text("商品 16"); expect(page.locator("[data-online]")).to_contain_text("在线")
    for m in ["文件", "编辑", "视图", "数据源", "工具", "帮助"]: expect(page.locator(f"[data-menu={m}]")).to_be_visible()
    expect(page.locator("[data-group-node=w_mon]")).to_contain_text("监控"); expect(page.locator("[data-node='商品监控'][data-kind=open]")).to_be_visible()
    nav(page, "goods"); expect(page.locator("[data-node='jd.com'][data-kind=filter]")).to_be_visible(); expect(page.locator("[data-node='耳机']")).to_be_visible(); shot(page, "01-main.png")
def test_products_grid(page):
    page.locator("[data-tab=t2]").click(); rows = V(page, "products").locator("[data-row]"); expect(rows).to_have_count(16)
    page.fill("[data-filter-q]", "iPhone"); expect(rows).to_have_count(3)
    page.click("th[data-sort=last_price]"); first = rows.first.locator("td").nth(8).inner_text(); page.click("th[data-sort=last_price]")
    expect(rows.first.locator("td").nth(8)).not_to_have_text(first)
    page.fill("[data-filter-q]", ""); page.select_option("[data-filter-cat]", "耳机"); expect(rows).to_have_count(2)
    nav(page, "goods"); page.click("[data-node='jd.com']"); expect(rows).to_have_count(6)   # 侧栏按来源平台筛选
    expect(page.locator("[data-chip=platform]")).to_contain_text("来源平台：jd.com"); page.click("[data-chip-clear=platform]"); expect(rows).to_have_count(16)
    page.click("[data-node='jd.com']"); expect(rows).to_have_count(6)
    page.evaluate("document.querySelectorAll('.vscroll').forEach(e=>e.scrollLeft=0)")   # 排序点击会把表格横向滚到价格列
    th = page.locator("th[data-sort=title]"); w0 = th.bounding_box()["width"]; h = th.locator("[data-resize]").bounding_box()
    page.mouse.move(h["x"] + 2, h["y"] + 5); page.mouse.down(); page.mouse.move(h["x"] + 82, h["y"] + 5, steps=5); page.mouse.up()
    assert th.bounding_box()["width"] > w0 + 50   # 列宽可调
    rows.first.click(); expect(page.locator("[data-props]")).to_contain_text("jd.com")   # 属性面板
    rows.first.click(button="right"); expect(page.locator("[data-ctx]")).to_be_visible(); shot(page, "02-context-menu.png")
    page.locator("[data-ctx] .mi", has_text="在新标签页打开详情").click(); expect(page.locator("[data-tab]")).to_have_count(3)
def test_split_panel_drag(page):
    side = page.locator("[data-side]"); w0 = side.bounding_box()["width"]; s = page.locator("[data-split-side]").bounding_box()
    page.mouse.move(s["x"] + 2, 300); page.mouse.down(); page.mouse.move(s["x"] + 102, 300, steps=5); page.mouse.up()
    assert side.bounding_box()["width"] > w0 + 80
def test_multi_product_tabs(page):
    page.locator("[data-tab=t2]").click(); nav(page, "goods"); page.click("[data-node='taobao.com']"); rows = V(page, "products").locator("[data-row]")
    rows.filter(has_text='iPhone').dblclick(); rows_tab = page.locator("[data-tab]")
    page.locator("[data-tab=t2]").click(); rows.filter(has_text='小米').dblclick(); expect(rows_tab).to_have_count(5)
    expect(V(page, "product").locator("[data-product-title]")).to_contain_text("小米")
    expect(V(page, "product").locator("[data-compare]")).to_contain_text("jd.com")
    V(page, "product").locator("[data-cost]").fill("5000"); V(page, "product").locator("[data-calc]").click()
    expect(V(page, "product").locator("[data-recommended]")).not_to_have_text("—"); shot(page, "03-product-tabs.png")
    page.locator("[data-tab]").last.locator("[data-close]").click(); expect(rows_tab).to_have_count(4)
    page.keyboard.press("Control+w"); expect(rows_tab).to_have_count(3)
def test_menu_tools(page):
    page.click("[data-menu=工具]"); page.click("[data-mi='跨平台比价']"); expect(V(page, "compare").locator("[data-group]").first).to_be_visible()
    assert V(page, "compare").locator("[data-group]").count() >= 5
    page.click("[data-menu=工具]"); page.click("[data-mi='定价模型']"); V(page, "pricing").locator("[data-pricing-run]").click()
    expect(V(page, "pricing").locator("[data-pricing-val]").last).not_to_have_text("—")
def test_search_ctrl_f(page):
    page.keyboard.press("Control+f"); v = V(page, "search"); expect(v).to_be_visible()
    expect(v.locator("[data-search-input]")).to_be_focused(); page.keyboard.type("headphones"); page.keyboard.press("Enter")
    expect(v.locator("[data-result]").first).to_contain_text("耳机"); expect(v.locator("[data-result]").first).to_contain_text("匹配"); shot(page, "04-search.png")
    v.locator("[data-mode=keyword]").click(); v.locator("[data-search-input]").fill("不存在xyz"); v.locator("[data-search-btn]").click(); expect(v.locator("[data-empty]")).to_be_visible()
    v.locator("[data-mode=chain]").click(); v.locator("[data-search-input]").fill("消费电子"); v.locator("[data-search-btn]").click(); expect(v.locator("[data-result]").nth(5)).to_be_visible()
    b = io.BytesIO(); Image.new("RGB", (64, 64), "blue").save(b, "PNG"); v.locator("[data-mode=image]").click()
    v.locator("[data-image-input]").set_input_files(files=[{"name": "q.png", "mimeType": "image/png", "buffer": b.getvalue()}]); expect(v.locator("[data-engine]")).to_contain_text("CLIP")
def test_ai_search(page):
    page.click("[data-tb='AI 搜索']"); v = V(page, "ai"); expect(v.locator("[data-ai-warn]")).to_contain_text("DEEPSEEK_API_KEY")
    v.locator("[data-ai-input]").fill("iPhone 16 最低价"); v.locator("[data-ai-btn]").click(); expect(v.locator("[data-ai-msg]")).to_contain_text("降级")
def test_sources_dialog(page):
    page.keyboard.press("Control+n"); f = page.locator("[data-source-form]"); expect(f).to_be_visible()
    f.locator("[data-f-name]").fill("私有ERP"); f.locator("[data-f-type]").select_option("rest"); f.locator("[data-f-platform]").fill("private-erp")
    f.locator("[data-f-url]").fill(MOCK + "/v1/goods"); f.locator("[data-f-headers]").fill('{"X-API-Key":"wrong"}'); f.locator("[data-f-items]").fill("data.items")
    f.locator("[data-f-mapping]").fill('{"external_id":"sku","title":"name","price":"pricing.amount","category":"cat"}')
    f.locator("[data-f-test]").click(); expect(f.locator("[data-test-result]")).to_contain_text("401")
    f.locator("[data-f-headers]").fill('{"X-API-Key":"secret-123"}'); f.locator("[data-f-test]").click(); expect(f.locator("[data-test-result]")).to_contain_text("获取 3 条")
    f.locator("[data-f-save]").click(); v = V(page, "sources"); expect(v.locator("[data-source-row]")).to_have_count(4)
    v.locator("[data-source-row]", has_text="私有ERP").locator("[data-fetch]").click(); expect(page.locator("[data-toast]").last).to_contain_text("3 条")
    v.locator("[data-source-row]", has_text="私有ERP").click(button="right"); page.locator("[data-ctx] .mi", has_text="删除").click()
    expect(v.locator("[data-source-row]")).to_have_count(3); page.locator("[data-toast-action]", has_text="撤销").last.click()   # 删除可撤销
    expect(v.locator("[data-source-row]")).to_have_count(4)
    v.locator("[data-source-row]", has_text="私有ERP").locator("[data-del]").click(); expect(v.locator("[data-source-row]")).to_have_count(3)
    page.click("[data-menu=数据源]"); page.click("[data-mi='立即抓取全部']"); expect(page.locator("[data-toast]").last).to_contain_text("3/3")
def test_catalog(page):
    nav(page, "work"); page.click("[data-node='平台目录']"); v = V(page, "catalog"); v.locator("[data-cat-country]").select_option("日本")
    expect(v.locator("[data-cat-total]")).to_have_text("6"); expect(v.locator("[data-cat-row]").first).to_contain_text("日本")
def test_command_palette_and_theme(page):
    page.keyboard.press("Control+k"); expect(page.locator("[data-palette]")).to_be_visible(); expect(page.locator("[data-palette-input]")).to_be_focused(); page.keyboard.type("主题"); expect(page.locator("[data-cmd]").first).to_contain_text("主题"); shot(page, "05-palette.png")
    page.keyboard.press("Enter"); expect(page.locator("html")).to_have_attribute("data-theme", "light"); expect(page.locator("[data-palette]")).to_be_hidden()
    assert page.evaluate("!document.activeElement.matches('[data-palette-input]')")   # 关闭面板后焦点不能残留在隐藏的输入框上
    page.keyboard.press("Control+k"); expect(page.locator("[data-palette-input]")).to_be_focused(); page.keyboard.type("打开商品: 有机")
    expect(page.locator("[data-cmd]").first).to_contain_text("绿茶"); page.keyboard.press("Enter"); expect(V(page, "product").locator("[data-product-title]")).to_contain_text("绿茶")
    shot(page, "06-light.png"); page.keyboard.press("Control+Shift+T"); expect(page.locator("html")).to_have_attribute("data-theme", "dark")
def test_help_dialog_and_toggle_panels(page):
    page.click("[data-menu=帮助]"); page.click("[data-mi='键盘快捷键']"); expect(page.locator("[data-dialog]")).to_contain_text("Ctrl+K"); page.keyboard.press("Escape")
    expect(page.locator("[data-dialog]")).to_be_hidden(); page.keyboard.press("Control+b"); expect(page.locator("[data-side]")).to_be_hidden(); page.keyboard.press("Control+b"); expect(page.locator("[data-side]")).to_be_visible()
EMOJI_RE = r"[\u2190-\u21FF\u2300-\u23FF\u25A0-\u27BF\u2B00-\u2BFF\uFF0B\U0001F000-\U0001FAFF]"
def test_deep_compare_from_table(page):
    page.evaluate("localStorage.clear()"); page.goto(API + "/"); page.locator("[data-tab=t2]").click(); v = V(page, "products")
    v.locator("[data-row]").filter(has_text="iPhone").locator("[data-check]").nth(0).check()
    v.locator("[data-row]").filter(has_text="iPhone").locator("[data-check]").nth(1).check()
    v.locator("[data-row]").filter(has_text="iPhone").locator("[data-check]").nth(2).check()
    v.locator("[data-deep-btn]").click(); d = V(page, "deep"); expect(d.locator("[data-deep-table]")).to_be_visible()
    expect(d.locator("[data-deep-open]")).to_have_count(3); expect(d.locator("[data-deep-row='当前价'] .cell-best")).to_have_count(1)
    expect(d.locator("[data-deep-row='平台']")).to_have_class(re.compile("rowdiff")); expect(d.locator("[data-deep-chart]")).to_be_visible()
    d.locator("[data-only-diff]").check(); expect(d.locator("[data-deep-row='币种']")).to_be_hidden(); d.locator("[data-only-diff]").uncheck()
    d.locator("[data-deep-remove]").last.click(); expect(d.locator("[data-deep-open]")).to_have_count(2)
    d.locator("[data-deep-open]").first.click(); expect(V(page, "product").locator("[data-product-title]")).to_contain_text("iPhone")
def test_deep_compare_from_group_ctx_and_detail(page):
    page.click("[data-menu=工具]"); page.click("[data-mi='跨平台比价']"); V(page, "compare").locator("[data-deep-group]").first.click()
    expect(V(page, "deep").locator("[data-deep-open]").first).to_be_visible()
    V(page, "deep").locator("[data-open-pid]").last.click(); expect(V(page, "product")).to_be_visible()
    V(page, "product").locator("[data-deep-from-detail]").click(); expect(V(page, "deep").locator("[data-deep-table]")).to_be_visible()
    page.locator("[data-tab=t2]").click(); V(page, "products").locator("[data-row]").filter(has_text="Switch").first.click(button="right")
    page.locator("[data-ctx] .mi", has_text="与同款商品深度对比").click(); expect(V(page, "deep").locator("[data-deep-open]")).to_have_count(2)
def test_confidence_badges_and_tooltip(page):
    page.locator("[data-tab=t2]").click(); v = V(page, "products"); expect(v.locator("[data-conf]").first).to_be_visible(); assert v.locator("[data-conf]").count() >= 5
    v.locator("[data-row]").filter(has_text="iPhone").first.dblclick(); p = V(page, "product")
    expect(p.locator("[data-current-conf] [data-conf]")).to_be_visible(); assert p.locator("[data-history] [data-conf]").count() >= 3
    expect(p.locator("[data-compare] [data-conf]").first).to_be_visible(); expect(p.locator("[data-pricing-conf] [data-conf]")).to_be_visible()
    p.locator("[data-current-conf] [data-conf]").hover(); expect(page.locator("[data-tooltip]")).to_contain_text("置信度"); expect(page.locator("[data-tooltip]")).to_contain_text("来源")
    expect(page.locator("[data-tooltip]")).to_contain_text("新鲜度"); shot(page, "07-conf-tooltip.png")
def test_platform_catalog_920_enable(page):
    page.click("[data-tb='平台目录']"); v = V(page, "catalog"); expect(v.locator("[data-cat-total]")).to_have_text("920")
    nav(page, "catalog"); expect(page.locator("[data-group-node=c_region]")).to_contain_text("920")
    page.click("[data-cat-dim=country]"); expect(page.locator("[data-group-node]")).to_have_count(1)   # 只展示一个维度
    page.fill("[data-nav-filter]", "日本"); page.locator("[data-node='日本']").click(); expect(v.locator("[data-cat-total]")).to_have_text("6")
    assert "共" in page.locator("[data-node='日本'] .badge").get_attribute("title"); page.fill("[data-nav-filter]", "")
    v.locator("[data-cat-row]").first.locator("[data-enable]").click(); expect(v.locator("[data-cat-row]").first.locator("[data-enable]")).to_contain_text("已启用")
    v.locator("[data-bulk-enable]").click(); expect(page.locator("[data-status-platforms]")).to_contain_text("已启用 6/920")
def test_no_emoji_or_symbol_icons(page):
    nav(page, "work")
    for t in ["dashboard", "products", "catalog"]: page.click(f"[data-node='{ {'dashboard':'仪表盘','products':'商品监控','catalog':'平台目录'}[t] }']")
    page.click("[data-menu=文件]"); txt = page.evaluate("document.body.innerText + [...document.querySelectorAll('[title],[placeholder]')].map(e=>(e.title||'')+(e.placeholder||'')).join(' ')")
    page.keyboard.press("Escape"); bad = re.findall(EMOJI_RE, txt); assert not bad, bad
    assert page.locator(".codicon").count() > 30 and page.evaluate("document.fonts.check('14px codicon')")
def test_activity_bar_toggle(page):
    nav(page, "settings"); expect(page.locator("[data-node='设置与集成']")).to_be_visible()
    page.click("[data-act='settings']"); expect(page.locator("[data-side]")).to_be_hidden()   # 再点一次折叠
    page.click("[data-act='work']"); expect(page.locator("[data-side][data-nav-view=work]")).to_be_visible()
    page.click("[data-group-node=w_ana]"); expect(page.locator("[data-node='比价']")).to_be_hidden(); page.click("[data-group-node=w_ana]")
def test_no_console_errors(page):
    assert page.errors == [], page.errors
