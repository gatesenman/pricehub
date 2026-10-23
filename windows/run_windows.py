"""PriceHub Windows 启动器：在 127.0.0.1:8800 启动服务（SQLite：data/pricehub.db），
就绪后优先用 pywebview 打开独立桌面窗口（应用图标）；仅当 pywebview 失败时回退系统浏览器并打印原因。"""
import os, sys, time, threading, socket, urllib.request, webbrowser, json, traceback
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(line_buffering=True)
except Exception: pass
HOST, PORT = "127.0.0.1", int(os.environ.get("PRICEHUB_PORT", "8800")); URL = f"http://{HOST}:{PORT}/"
WINDOW_TITLE = "PriceHub"
APP_ID = "PriceHub.Desktop.1"
os.environ.pop("PRICEHUB_DB_URL", None); os.environ.pop("PRICEHUB_PG_SCHEMA", None)
os.environ.setdefault("PRICEHUB_DB", os.path.join(ROOT, "data", "pricehub.db")); os.environ.setdefault("PRICEHUB_QUEUE", "memory")
os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)

def icon_path():
    repo = os.path.dirname(ROOT)
    for p in (
        os.path.join(ROOT, "PriceHub.ico"),
        os.path.join(ROOT, "installer", "PriceHub.ico"),
        os.path.join(repo, "app", "static", "icons", "pricehub.ico"),
        os.path.join(repo, "windows", "installer", "PriceHub.ico"),
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

def ready(timeout=1.0):
    try:
        with urllib.request.urlopen(URL + "readyz", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("status") == "ready"
    except Exception:
        return False

def port_busy():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0

def open_ui(block):
    try:
        import webview
        ico = icon_path()
        print("正在打开桌面窗口：" + WINDOW_TITLE + (("  图标：" + ico) if ico else ""))
        set_app_id()
        webview.create_window(WINDOW_TITLE, URL, width=1440, height=900, min_size=(1024, 640))
        if ico:
            webview.start(icon=ico)
        else:
            webview.start()
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
    if port_busy():
        if ready():
            print("PriceHub 已在运行，打开界面：" + URL)
            open_ui(block=False)
            return
        print(f"端口 {PORT} 被其他程序占用。请关闭占用该端口的程序，或设置环境变量 PRICEHUB_PORT 换一个端口后再启动。")
        sys.exit(2)
    import uvicorn
    cfg = uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="warning")
    srv = uvicorn.Server(cfg); th = threading.Thread(target=srv.run, daemon=True); th.start()
    print("正在启动 PriceHub（首次加载约 10–30 秒）…")
    t0 = time.time()
    while not ready():
        if not th.is_alive(): print("启动失败，请查看上面的错误信息。"); sys.exit(1)
        if time.time() - t0 > 180: print("等待超时（180 秒），请查看上面的错误信息。"); sys.exit(1)
        time.sleep(1)
    print("PriceHub 已就绪：" + URL + "（数据库：" + os.environ["PRICEHUB_DB"] + "）")
    if os.environ.get("PRICEHUB_NO_BROWSER"):
        th.join(); return
    open_ui(block=True); srv.should_exit = True

if __name__ == "__main__":
    main()
