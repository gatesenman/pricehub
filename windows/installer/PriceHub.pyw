"""PriceHub 桌面启动器（安装版，pythonw / PriceHub.exe 无控制台）。
- 用户数据在 %APPDATA%\\PriceHub（升级/重装不丢）：首次运行把安装目录里的 seed\\pricehub.db 复制过去；
- 在 127.0.0.1:8800 启动服务（SQLite），就绪后优先用 pywebview 打开独立桌面窗口；
- 窗口 / 任务栏使用安装目录里的 PriceHub.ico；
- 仅当 pywebview 真正失败时才回退到系统浏览器，并写入日志说明原因；
- 已在运行时再开一个桌面窗口（或浏览器）；参数 --stop 停止后台服务。
日志：%APPDATA%\\PriceHub\\logs\\pricehub.log"""
from __future__ import annotations
import os, sys, time, json, shutil, socket, threading, traceback, urllib.request, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
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


def icon_path() -> str | None:
    for p in (
        os.path.join(ROOT, "PriceHub.ico"),
        os.path.join(ROOT, "app", "static", "icons", "pricehub.ico"),
    ):
        if os.path.isfile(p):
            return p
    return None


def set_app_user_model_id() -> None:
    """让任务栏把本进程当成 PriceHub，而不是 pythonw。"""
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


def ready(t: float = 1.0) -> bool:
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


def open_desktop_window(block: bool) -> bool:
    """优先 pywebview 独立窗口；失败则回退浏览器并记录原因。成功返回 True。"""
    if os.environ.get("PRICEHUB_NO_BROWSER"):
        return False
    try:
        import webview  # bundled in installer
        ico = icon_path()
        log("opening pywebview window", WINDOW_TITLE, URL, "icon", ico)
        os.environ.setdefault(
            "WEBVIEW2_USER_DATA_FOLDER",
            os.path.join(DATA, "webview2"),
        )
        set_app_user_model_id()
        webview.create_window(
            WINDOW_TITLE,
            URL,
            width=1440,
            height=900,
            min_size=(1024, 640),
        )
        # pywebview 6：窗口图标通过 start(icon=...) 注入，Windows winforms 会读 _state['icon']
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
        uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=PORT,
            log_level="warning",
            log_config=None,
        )
    )
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    log("starting PriceHub", sys.version, "db", db)

    t0 = time.time()
    while not ready():
        if not th.is_alive():
            msg("PriceHub 启动失败，请查看日志：" + os.path.join(DATA, "logs", "pricehub.log"))
            return
        if time.time() - t0 > 180:
            msg("PriceHub 启动超时，请查看日志：" + os.path.join(DATA, "logs", "pricehub.log"))
            return
        time.sleep(0.5)

    log("ready", URL)
    try:
        open_desktop_window(block=True)
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
            open_desktop_window(block=False)
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
