"""v7 界面 e2e：单机免登录 → 创建所有者并开启团队模式 → 登录页 → 工作区切换 → 成员邀请（只生成链接）→ 受邀人注册 → 用量页 → SSO 登录（本地模拟 OIDC）。无控制台报错。"""
import os, sys, time, tempfile, subprocess, httpx, pytest
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PY = sys.executable
API, MOCK, IDP = "http://127.0.0.1:8982", "http://127.0.0.1:8981", "http://127.0.0.1:8971"
SHOT = os.path.join(ROOT, "screenshots", "v7"); os.makedirs(SHOT, exist_ok=True)
def wait(u):
    for _ in range(180):
        try:
            if httpx.get(u, timeout=2).status_code < 500: return
        except Exception: pass
        time.sleep(1)
@pytest.fixture(scope="module")
def env():
    e = dict(os.environ, PRICEHUB_DB=tempfile.mktemp(suffix=".db"), PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1"); e["PRICEHUB_EMB_CACHE"] = e["PRICEHUB_DB"] + ".npz"
    ps = [subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_private_api:app", "--port", "8981"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "tests.mock_oidc:app", "--port", "8971"], cwd=ROOT),
          subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", "8982"], cwd=ROOT, env=e)]
    for u in (MOCK + "/docs", IDP + "/jwks", API + "/healthz"): wait(u)
    httpx.post(API + "/api/sources", json={"name": "演示", "type": "mock", "config": {"platform": "taobao.com", "seed": 1}}, timeout=60); httpx.post(API + "/api/sources/1/fetch", timeout=120)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox","--disable-dev-shm-usage"]); errs = []
        def page():
            ctx = b.new_context(viewport={"width": 1440, "height": 900}); pg = ctx.new_page()
            pg.on("console", lambda m: m.type == "error" and "401" not in m.text and "403" not in m.text and errs.append(m.text)); pg.on("pageerror", lambda x: errs.append(str(x))); return pg
        yield page, errs; b.close()
    for x in ps: x.terminate()
def test_team_ui(env):
    page, errs = env; pg = page()
    pg.goto(API + "/"); pg.wait_for_timeout(1500)
    expect(pg.locator("[data-ws-switch]")).to_contain_text("默认工作区"); expect(pg.locator("[data-login]")).to_be_hidden()   # 单机免登录
    pg.click("[data-ws-switch]"); pg.screenshot(path=os.path.join(SHOT, "01_单机模式工作区菜单.png"))
    pg.locator(".wsmenu .mi", has_text="团队与成员").click(); pg.wait_for_timeout(800)
    v = pg.locator("[data-v7-team]"); v.locator("[data-owner-email]").fill("owner@example.com"); v.locator("[data-owner-pw]").fill("secret123"); v.locator("[data-owner-create]").click(); pg.wait_for_timeout(800)
    v.locator("[data-team-mode]").check(); pg.wait_for_timeout(800); expect(v).to_contain_text("已开启")
    v.locator("[data-invite-email]").fill("viewer@example.com"); v.locator("[data-invite-role]").select_option("viewer"); v.locator("[data-invite-create]").click(); pg.wait_for_timeout(800)
    link = v.locator("[data-invite-link] .mono").inner_text(); assert "/?invite=" in link
    v.locator("[data-oidc-issuer]").fill(IDP); v.locator("[data-oidc-client]").fill("pricehub"); v.locator("input[type=password]").last.fill("mock-secret"); v.locator("[data-oidc-save]").click(); pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(SHOT, "02_团队与成员.png"), full_page=True)
    # 用量页
    pg.click("[data-ws-switch]"); pg.locator(".wsmenu .mi", has_text="用量与配额").click(); pg.wait_for_timeout(800)
    u = pg.locator("[data-v7-usage]"); expect(u.locator("[data-usage='skus']")).to_contain_text("不限")
    pg.screenshot(path=os.path.join(SHOT, "03_用量与配额.png"), full_page=True)
    # 新建工作区并切换
    pg.once("dialog", lambda d: d.accept("二号店")); pg.click("[data-ws-switch]"); pg.locator(".wsmenu .mi", has_text="新建工作区").click(); pg.wait_for_timeout(2500)
    expect(pg.locator("[data-ws-switch]")).to_contain_text("二号店"); pg.click("[data-ws-switch]"); pg.screenshot(path=os.path.join(SHOT, "04_工作区切换.png"))
    pg.locator("[data-ws-item='1']").click(); pg.wait_for_timeout(2500); expect(pg.locator("[data-ws-switch]")).to_contain_text("默认工作区")
    # 受邀人：登录页 → 注册
    p2 = page(); p2.goto(link); p2.wait_for_timeout(1500); L = p2.locator("[data-login]"); expect(L).to_be_visible(); expect(L.locator("[data-login-invite]")).to_contain_text("只读")
    expect(L.locator("[data-login-sso]")).to_be_visible(); p2.screenshot(path=os.path.join(SHOT, "05_登录页_邀请注册.png"))
    L.locator("[data-login-pw]").fill("secret123"); L.locator("[data-login-go]").click(); p2.wait_for_timeout(2500)
    expect(p2.locator("[data-login]")).to_be_hidden(); expect(p2.locator("[data-ws-switch]")).to_contain_text("只读")
    # 未登录 → 登录页 → 错误密码提示 → 用模拟 SSO 登录
    p3 = page(); p3.goto(API + "/"); p3.wait_for_timeout(1500); expect(p3.locator("[data-login]")).to_be_visible()
    L3 = p3.locator("[data-login]"); L3.locator("[data-login-email]").fill("owner@example.com"); L3.locator("[data-login-pw]").fill("wrong-pass"); L3.locator("[data-login-go]").click(); p3.wait_for_timeout(800)
    expect(L3.locator("[data-login-error]")).to_be_visible(); p3.screenshot(path=os.path.join(SHOT, "06_登录失败提示.png"))
    L3.locator("[data-login-sso]").click(); p3.wait_for_timeout(3000)
    assert p3.url.startswith(API), p3.url
    assert "login_error" not in p3.url, p3.url
    expect(p3.locator("[data-login]")).to_be_hidden(); expect(p3.locator("[data-ws-switch]")).to_contain_text("sso.user 的工作区")   # SSO 新用户自动建号与个人工作区
    p3.screenshot(path=os.path.join(SHOT, "07_SSO登录后.png"))
    assert not errs, errs
