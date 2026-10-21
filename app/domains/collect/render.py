"""无头浏览器渲染采集（Playwright Chromium）。
可配置渲染池：PRICEHUB_RENDER_WORKERS（默认 1）、PRICEHUB_RENDER_QUEUE（默认 32）。
多个后台线程各自持有浏览器实例，任务经共享队列分发。
"""
import os, queue, threading, time
from concurrent.futures import Future
_Q = queue.Queue(maxsize=int(os.environ.get("PRICEHUB_RENDER_QUEUE", "32")))
_T = {"threads": [], "ok": None, "error": None, "started": 0, "done": 0, "failed": 0}
ENABLED = os.environ.get("PRICEHUB_NO_RENDER") != "1"
POOL_SIZE = max(1, int(os.environ.get("PRICEHUB_RENDER_WORKERS", "1")))
def _worker(wid):
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start(); browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        if _T["ok"] is None: _T["ok"] = True
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
                fut.set_result({"status": status, "html": html, "url": pg.url, "ms": int((time.time() - t0) * 1000), "screenshot": shot, "worker": wid})
                _T["done"] += 1
            finally: pg.close()
        except Exception as e: _T["failed"] += 1; fut.set_exception(e)
def _ensure():
    if _T["threads"]: return
    for i in range(POOL_SIZE):
        t = threading.Thread(target=_worker, args=(i,), daemon=True, name=f"renderer-{i}"); t.start(); _T["threads"].append(t)
    _T["started"] = POOL_SIZE
def render(url, screenshot=None, timeout=25, wait_ms=4000, proxy=None):
    if not ENABLED: raise RuntimeError("已关闭无头浏览器渲染（PRICEHUB_NO_RENDER=1）")
    _ensure(); f = Future()
    if proxy is None:
        try:
            from app import net; proxy = net.next_proxy()
        except Exception: proxy = None
    try: _Q.put((url, screenshot, timeout, wait_ms, proxy, f), timeout=5)
    except queue.Full: raise RuntimeError(f"渲染队列已满（上限 {_Q.maxsize}），请调高 PRICEHUB_RENDER_QUEUE 或稍后再试")
    return f.result(timeout=timeout + 30)
def configure(workers=None, queue_size=None):
    """运行时调整（新 workers 仅在尚未启动时生效；队列上限改 maxsize 需重建，这里只返回当前配置）。"""
    global POOL_SIZE
    if workers is not None and not _T["threads"]: POOL_SIZE = max(1, int(workers))
    return status()
def status():
    return {"enabled": ENABLED, "started": bool(_T["threads"]), "ok": _T["ok"], "error": _T["error"],
            "queue": _Q.qsize(), "queue_max": _Q.maxsize, "workers": POOL_SIZE, "workers_alive": sum(1 for t in _T["threads"] if t.is_alive()),
            "done": _T["done"], "failed": _T["failed"]}
