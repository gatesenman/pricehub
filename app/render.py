"""无头浏览器渲染采集（Playwright Chromium）。
一个后台线程持有浏览器实例，其它线程通过队列提交任务（同步 API 不能跨线程共享）。
用途：需要浏览器脚本才能显示价格的页面（SPA）、MAP 违规取证整页截图、连通性复检。"""
import os, queue, threading, time
from concurrent.futures import Future
_Q = queue.Queue(); _T = {"thread": None, "ok": None, "error": None}
ENABLED = os.environ.get("PRICEHUB_NO_RENDER") != "1"
def _worker():
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start(); browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"]); _T["ok"] = True
    except Exception as e:
        _T["ok"] = False; _T["error"] = str(e)
        while True:
            job = _Q.get(); job[-1].set_exception(RuntimeError(f"无头浏览器不可用：{e}"))
    ctxs = {}
    while True:
        url, shot, timeout, wait_ms, proxy, fut = _Q.get()
        try:
            key = proxy or ""
            if key not in ctxs:
                ctxs[key] = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN", proxy={"server": proxy} if proxy else None,
                                                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
            pg = ctxs[key].new_page(); t0 = time.time()
            try:
                resp = pg.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                try: pg.wait_for_load_state("networkidle", timeout=wait_ms)
                except Exception: pass
                html = pg.content(); status = resp.status if resp else 0
                if shot: os.makedirs(os.path.dirname(shot), exist_ok=True); pg.screenshot(path=shot, full_page=True)
                fut.set_result({"status": status, "html": html, "url": pg.url, "ms": int((time.time() - t0) * 1000), "screenshot": shot})
            finally: pg.close()
        except Exception as e: fut.set_exception(e)
def _ensure():
    if not _T["thread"]:
        _T["thread"] = threading.Thread(target=_worker, daemon=True, name="renderer"); _T["thread"].start()
def render(url, screenshot=None, timeout=25, wait_ms=4000, proxy=None):
    """渲染页面，返回 {status, html, url, ms, screenshot}；失败抛异常。"""
    if not ENABLED: raise RuntimeError("已关闭无头浏览器渲染（PRICEHUB_NO_RENDER=1）")
    _ensure(); f = Future()
    if proxy is None:
        try:
            from . import net; proxy = net.next_proxy()
        except Exception: proxy = None
    _Q.put((url, screenshot, timeout, wait_ms, proxy, f)); return f.result(timeout=timeout + 30)
def status(): return {"enabled": ENABLED, "started": bool(_T["thread"]), "ok": _T["ok"], "error": _T["error"], "queue": _Q.qsize()}
