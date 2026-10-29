"""PriceHub Windows 启动器：在 127.0.0.1:8800 启动服务（SQLite：data/pricehub.db），
v14：先显示启动闪屏，服务就绪后再进入工作台；优先 pywebview 独立桌面窗口。"""
import os, sys, time, threading, socket, urllib.request, webbrowser, json, traceback
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)
# 源码布局：仓库根在上一级
REPO = os.path.dirname(ROOT)
if REPO not in sys.path:
    sys.path.insert(0, REPO)
try: sys.stdout.reconfigure(line_buffering=True)
except Exception: pass
HOST, PORT = "127.0.0.1", int(os.environ.get("PRICEHUB_PORT", "8800")); URL = f"http://{HOST}:{PORT}/"
WINDOW_TITLE = "PriceHub"
APP_ID = "PriceHub.Desktop.1"
os.environ.pop("PRICEHUB_DB_URL", None); os.environ.pop("PRICEHUB_PG_SCHEMA", None)
os.environ.setdefault("PRICEHUB_DB", os.path.join(ROOT, "data", "pricehub.db") if os.path.isdir(os.path.join(ROOT, "data")) else os.path.join(REPO, "data", "pricehub.db"))
os.environ.setdefault("PRICEHUB_QUEUE", "memory")
os.environ.setdefault("PRICEHUB_NO_ST", "1")
os.environ.setdefault("PRICEHUB_NO_CLIP", "1")
os.makedirs(os.path.join(os.path.dirname(os.environ["PRICEHUB_DB"]), "..", "logs") if False else os.path.join(REPO if os.path.isdir(os.path.join(REPO, "logs")) else ROOT, "logs"), exist_ok=True)

BOOT_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>PriceHub</title>
<style>html,body{margin:0;height:100%;background:#1e1e1e;color:#d4d4d4;font:13px/1.4 Segoe UI,system-ui,sans-serif}
.wrap{height:100%;display:grid;place-items:center}.card{text-align:center;padding:32px 40px;border-radius:12px;background:#252526;border:1px solid #3c3c3c;min-width:260px}
.mark{width:64px;height:64px;margin:0 auto 12px;border-radius:14px;background:linear-gradient(145deg,#0e7ad1,#1f8ad2);display:grid;place-items:center}
h1{margin:0 0 6px;font-size:18px;color:#3794ff}p{margin:0;color:#8b8b8b}
.bar{width:160px;height:3px;background:#2d2d30;overflow:hidden;margin:14px auto 0;border-radius:2px}
.bar>i{display:block;height:100%;width:40%;background:#0e7ad1;animation:b 1.1s ease-in-out infinite}
@keyframes b{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}</style></head>
<body><div class="wrap"><div class="card"><div class="mark"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></div>
<h1>PriceHub</h1><p>正在启动服务…</p><div class="bar"><i></i></div></div></div></body></html>"""

def icon_path():
    for p in (
        os.path.join(ROOT, "PriceHub.ico"),
        os.path.join(ROOT, "installer", "PriceHub.ico"),
        os.path.join(REPO, "app", "static", "icons", "pricehub.ico"),
        os.path.join(REPO, "windows", "installer", "PriceHub.ico"),
    ):
        if os.path.isfile(p):
            return p
    return None

def set_app_id():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

def ready(timeout=0.4):
    try:
        with urllib.request.urlopen(URL + "readyz", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("status") == "ready"
    except Exception:
        return False

def port_busy():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0

def open_ui(block, html=None, navigate=False, url=None):
    try:
        import webview
        ico = icon_path()
        print("正在打开桌面窗口：" + WINDOW_TITLE + (("  图标：" + ico) if ico else ""))
        set_app_id()
        kw = dict(title=WINDOW_TITLE, width=1440, height=900, min_size=(1024, 640))
        if html is not None:
            window = webview.create_window(html=html, **kw)
        else:
            window = webview.create_window(url=url or URL, **kw)
        if navigate:
            def _nav():
                t0 = time.time()
                while not ready():
                    if time.time() - t0 > 180: return
                    time.sleep(0.15)
                try: window.load_url(URL)
                except Exception as e: print("load_url failed", e)
            threading.Thread(target=_nav, daemon=True).start()
        if ico: webview.start(icon=ico)
        else: webview.start()
        return True
    except Exception as e:
        print("pywebview 不可用，回退系统浏览器。原因：", repr(e))
        traceback.print_exc()
        webbrowser.open(URL)
        if block:
            print("已在浏览器打开 " + URL + "。关闭本窗口即停止 PriceHub。")
            try:
                while True: time.sleep(3600)
            except KeyboardInterrupt: pass
        return False

def main():
    set_app_id()
    # 若从 windows/ 目录跑，把工作目录切到仓库根以便 import app
    if os.path.isdir(os.path.join(REPO, "app")):
        os.chdir(REPO); sys.path.insert(0, REPO)
    if port_busy():
        if ready():
            print("PriceHub 已在运行，打开界面：" + URL)
            open_ui(block=False, url=URL)
            return
        print(f"端口 {PORT} 被其他程序占用。请关闭占用该端口的程序，或设置环境变量 PRICEHUB_PORT 换一个端口后再启动。")
        sys.exit(2)
    import uvicorn
    cfg = uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="warning")
    srv = uvicorn.Server(cfg); th = threading.Thread(target=srv.run, daemon=True); th.start()
    print("正在启动 PriceHub…")
    if os.environ.get("PRICEHUB_NO_BROWSER"):
        t0 = time.time()
        while not ready():
            if not th.is_alive(): print("启动失败"); sys.exit(1)
            if time.time() - t0 > 180: print("等待超时"); sys.exit(1)
            time.sleep(0.15)
        print("PriceHub 已就绪：" + URL); th.join(); return
    open_ui(block=True, html=BOOT_HTML, navigate=True); srv.should_exit = True

if __name__ == "__main__":
    main()
