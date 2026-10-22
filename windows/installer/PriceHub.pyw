"""PriceHub 桌面启动器（安装版，pythonw 无控制台）。
- 用户数据在 %APPDATA%\\PriceHub（升级/重装不丢）：首次运行把安装目录里的 seed\\pricehub.db 复制过去；
- 在 127.0.0.1:8800 启动服务（SQLite），就绪后打开浏览器（装了 pywebview 则用独立窗口）；
- 已在运行时只打开浏览器；参数 --stop 停止后台服务。日志：%APPDATA%\\PriceHub\\logs\\pricehub.log"""
import os, sys, time, json, shutil, socket, threading, urllib.request, webbrowser
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT); os.chdir(ROOT)
DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PriceHub"); os.makedirs(os.path.join(DATA, "logs"), exist_ok=True)
LOG = open(os.path.join(DATA, "logs", "pricehub.log"), "a", encoding="utf-8", buffering=1); sys.stdout = sys.stderr = LOG
HOST, PORT = "127.0.0.1", int(os.environ.get("PRICEHUB_PORT", "8800")); URL = f"http://{HOST}:{PORT}/"; PIDF = os.path.join(DATA, "pricehub.pid")
def log(*a): print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)
def msg(text):
    try:
        import ctypes; ctypes.windll.user32.MessageBoxW(0, text, "PriceHub", 0x40)
    except Exception: log(text)
def ready(t=1.0):
    try:
        with urllib.request.urlopen(URL + "readyz", timeout=t) as r: return json.loads(r.read().decode()).get("status") == "ready"
    except Exception: return False
def busy():
    with socket.socket() as s: s.settimeout(0.5); return s.connect_ex((HOST, PORT)) == 0
def stop():
    try: pid = int(open(PIDF).read().strip())
    except Exception: return
    try:
        import signal; os.kill(pid, signal.SIGTERM); log("stopped", pid)
    except Exception as e: log("stop failed", e)
def show():
    if os.environ.get("PRICEHUB_NO_BROWSER"): return
    webbrowser.open(URL)
def main():
    if "--stop" in sys.argv: stop(); return
    if busy():
        if ready(): show(); return
        msg(f"端口 {PORT} 被其他程序占用，PriceHub 无法启动。"); return
    db = os.path.join(DATA, "pricehub.db")
    if not os.path.exists(db):
        seed = os.path.join(ROOT, "seed", "pricehub.db")
        if os.path.exists(seed): shutil.copy2(seed, db); log("seed db copied to", db)
    os.environ.update(PRICEHUB_DB=db, PRICEHUB_QUEUE="memory"); os.environ.pop("PRICEHUB_DB_URL", None)
    open(PIDF, "w").write(str(os.getpid()))
    import uvicorn
    srv = uvicorn.Server(uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="warning", log_config=None))
    def opener():
        t0 = time.time()
        while time.time() - t0 < 180:
            if ready(): log("ready", URL, "db", db); show(); return
            time.sleep(0.5)
        msg("PriceHub 启动超时，请查看日志：" + os.path.join(DATA, "logs", "pricehub.log"))
    threading.Thread(target=opener, daemon=True).start()
    log("starting PriceHub", sys.version)
    try: srv.run()
    except Exception as e: log("server error", repr(e)); msg("PriceHub 启动失败：" + repr(e))
    finally:
        try: os.remove(PIDF)
        except Exception: pass
if __name__ == "__main__":
    try: main()
    except Exception as e:
        import traceback; traceback.print_exc(); msg("PriceHub 出错：" + repr(e))
