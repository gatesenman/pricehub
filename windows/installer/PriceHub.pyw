"""PriceHub 桌面启动器（安装版，pythonw / PriceHub.exe 无控制台）。
v14：窗口先显示启动闪屏，服务在后台起来后再跳到工作台，缩短体感冷启动。
- 用户数据在 %APPDATA%\\PriceHub（升级/重装不丢）：首次运行把安装目录里的 seed\\pricehub.db 复制过去；
- 在 127.0.0.1:8800 启动服务（SQLite），优先用 pywebview 独立桌面窗口；
- 窗口 / 任务栏使用安装目录里的 PriceHub.ico；
- 仅当 pywebview 真正失败时才回退到系统浏览器，并写入日志说明原因；
- 已在运行时再开一个桌面窗口（或浏览器）；参数 --stop 停止后台服务。
日志：%APPDATA%\\PriceHub\\logs\\pricehub.log"""
from __future__ import annotations
import os, sys, time, json, shutil, socket, threading, traceback, urllib.request, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
    os.environ.pop(_k, None)
sys.path.insert(0, ROOT)
_sp = os.path.join(ROOT, "python", "Lib", "site-packages")
if os.path.isdir(_sp) and _sp not in sys.path:
    sys.path.insert(0, _sp)
os.chdir(ROOT)
DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PriceHub")
os.makedirs(os.path.join(DATA, "logs"), exist_ok=True)
LOG = open(os.path.join(DATA, "logs", "pricehub.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = LOG

HOST = "127.0.0.1"
PORT = int(os.environ.get("PRICEHUB_PORT", "8800"))
URL = f"http://{HOST}:{PORT}/"
PIDF = os.path.join(DATA, "pricehub.pid")
WINDOW_TITLE = "PriceHub"
APP_ID = "PriceHub.Desktop.1"

# Windows 精简包默认不加载大模型，进一步加快冷启动
os.environ.setdefault("PRICEHUB_NO_ST", "1")
os.environ.setdefault("PRICEHUB_NO_CLIP", "1")

BOOT_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>PriceHub</title>
<style>html,body{margin:0;height:100%;background:#1e1e1e;color:#d4d4d4;font:13px/1.4 Segoe UI,system-ui,sans-serif}
.wrap{height:100%;display:grid;place-items:center}.card{text-align:center;padding:32px 40px;border-radius:12px;background:#252526;border:1px solid #3c3c3c;box-shadow:0 12px 40px #0008;min-width:260px}
.mark{width:64px;height:64px;margin:0 auto 12px;border-radius:14px;background:linear-gradient(145deg,#0e7ad1,#1f8ad2);display:grid;place-items:center}
h1{margin:0 0 6px;font-size:18px;color:#3794ff}p{margin:0;color:#8b8b8b}
.bar{width:160px;height:3px;border-radius:2px;background:#2d2d30;overflow:hidden;margin:14px auto 0}
.bar>i{display:block;height:100%;width:40%;background:#0e7ad1;animation:b 1.1s ease-in-out infinite}
@keyframes b{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}</style></head>
<body><div class="wrap"><div class="card"><div class="mark"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></div>
<h1>PriceHub</h1><p id="m">正在启动服务…</p><div class="bar"><i></i></div></div></div>
<script>let n=0,a=['正在启动服务…','加载数据引擎…','准备桌面窗口…','即将进入工作台…'];setInterval(()=>{n=(n+1)%a.length;document.getElementById('m').textContent=a[n]},1200)</script>
</body></html>"""


def icon_path() -> str | None:
    for p in (
        os.path.join(ROOT, "PriceHub.ico"),
        os.path.join(ROOT, "app", "static", "icons", "pricehub.ico"),
    ):
        if os.path.isfile(p):
            return p
    return None


def set_app_user_model_id() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        log("SetCurrentProcessExplicitAppUserModelID failed", e)


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def msg(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "PriceHub", 0x40)
    except Exception:
        log(text)


def ready(t: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(URL + "readyz", timeout=t) as r:
            return json.loads(r.read().decode()).get("status") == "ready"
    except Exception:
        return False


def busy() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def stop() -> None:
    try:
        pid = int(open(PIDF).read().strip())
    except Exception:
        return
    try:
        import signal
        os.kill(pid, signal.SIGTERM)
        log("stopped", pid)
    except Exception as e:
        log("stop failed", e)


def open_desktop_window(block: bool, start_url: str | None = None, html: str | None = None,
                        navigate_when_ready: bool = False) -> bool:
    if os.environ.get("PRICEHUB_NO_BROWSER"):
        return False
    try:
        import webview
        ico = icon_path()
        log("opening pywebview window", WINDOW_TITLE, start_url or "(splash)", "icon", ico)
        os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", os.path.join(DATA, "webview2"))
        set_app_user_model_id()
        kw = dict(title=WINDOW_TITLE, width=1440, height=900, min_size=(1024, 640))
        if html is not None:
            window = webview.create_window(html=html, **kw)
        else:
            window = webview.create_window(url=start_url or URL, **kw)

        def _nav():
            t0 = time.time()
            while not ready():
                if time.time() - t0 > 180:
                    log("ready timeout while showing splash")
                    try:
                        window.evaluate_js("document.getElementById('m').textContent='启动超时，请查看日志'")
                    except Exception:
                        pass
                    return
                time.sleep(0.15)
            log("ready, navigating to", URL)
            try:
                window.load_url(URL)
            except Exception as e:
                log("load_url failed", e)

        if navigate_when_ready:
            threading.Thread(target=_nav, daemon=True).start()
        if ico:
            webview.start(icon=ico)
        else:
            webview.start()
        log("pywebview window closed")
        return True
    except Exception as e:
        log("pywebview failed, falling back to system browser:", repr(e))
        log(traceback.format_exc())
        try:
            webbrowser.open(URL)
            log("opened system browser", URL)
        except Exception as e2:
            log("browser open also failed:", repr(e2))
            msg("无法打开界面。请手动访问 " + URL + "\n详情见日志。")
            return False
        if block:
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        return False


def ensure_seed_db(db: str) -> None:
    if os.path.exists(db):
        return
    seed = os.path.join(ROOT, "seed", "pricehub.db")
    if os.path.exists(seed):
        shutil.copy2(seed, db)
        log("seed db copied to", db)


def run_server_and_window() -> None:
    db = os.path.join(DATA, "pricehub.db")
    ensure_seed_db(db)
    os.environ["PRICEHUB_DB"] = db
    os.environ["PRICEHUB_QUEUE"] = "memory"
    os.environ.pop("PRICEHUB_DB_URL", None)
    os.environ.pop("PRICEHUB_PG_SCHEMA", None)

    open(PIDF, "w").write(str(os.getpid()))
    import uvicorn

    srv = uvicorn.Server(
        uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="warning", log_config=None)
    )
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    log("starting PriceHub", sys.version, "db", db)

    # 先开闪屏窗口，服务在后台就绪后跳转——体感冷启动明显更快
    try:
        open_desktop_window(block=True, html=BOOT_HTML, navigate_when_ready=True)
    finally:
        srv.should_exit = True
        try:
            os.remove(PIDF)
        except Exception:
            pass
        th.join(timeout=5)


def main() -> None:
    set_app_user_model_id()
    if "--stop" in sys.argv:
        stop()
        return
    if busy():
        if ready():
            log("already running, opening UI only", URL)
            open_desktop_window(block=False, start_url=URL)
            return
        msg(f"端口 {PORT} 被其他程序占用，PriceHub 无法启动。")
        return
    run_server_and_window()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        msg("PriceHub 出错：" + repr(e))
