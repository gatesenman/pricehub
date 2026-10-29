"""PriceHub 桌面窗口启动器（pywebview）。
用法：
  python desktop.py                 # 内嵌模式：本进程启动 FastAPI + 闪屏，就绪后进入工作台
  python desktop.py --url http://服务器:8800   # 瘦客户端模式：连接已运行的 PriceHub 服务
"""
import argparse, os, socket, threading, time, sys, urllib.request, json

BOOT_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>PriceHub</title>
<style>html,body{margin:0;height:100%;background:#1e1e1e;color:#d4d4d4;font:13px/1.4 system-ui,sans-serif}
.wrap{height:100%;display:grid;place-items:center}.card{text-align:center;padding:32px 40px;border-radius:12px;background:#252526;border:1px solid #3c3c3c;min-width:260px}
.mark{width:64px;height:64px;margin:0 auto 12px;border-radius:14px;background:linear-gradient(145deg,#0e7ad1,#1f8ad2);display:grid;place-items:center}
h1{margin:0 0 6px;font-size:18px;color:#3794ff}p{margin:0;color:#8b8b8b}
.bar{width:160px;height:3px;background:#2d2d30;overflow:hidden;margin:14px auto 0;border-radius:2px}
.bar>i{display:block;height:100%;width:40%;background:#0e7ad1;animation:b 1.1s ease-in-out infinite}
@keyframes b{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}</style></head>
<body><div class="wrap"><div class="card"><div class="mark"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></div>
<h1>PriceHub</h1><p>正在启动服务…</p><div class="bar"><i></i></div></div></div></body></html>"""

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def icon_path():
    root = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(root, "windows", "installer", "PriceHub.ico"),
        os.path.join(root, "app", "static", "icons", "pricehub.ico"),
        os.path.join(root, "PriceHub.ico"),
    ):
        if os.path.isfile(p):
            return p
    return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--url"); ap.add_argument("--port", type=int); a = ap.parse_args()
    url = a.url
    port = None
    if not url:
        import uvicorn
        port = a.port or free_port(); url = f"http://127.0.0.1:{port}"
        threading.Thread(target=lambda: uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    import webview
    ico = icon_path()
    if a.url:
        webview.create_window("PriceHub", url, width=1440, height=900, min_size=(1024, 640))
        webview.start(icon=ico) if ico else webview.start()
        return
    window = webview.create_window("PriceHub", html=BOOT_HTML, width=1440, height=900, min_size=(1024, 640))
    def _nav():
        for _ in range(1200):
            try:
                with urllib.request.urlopen(url + "/readyz", timeout=0.4) as r:
                    if json.loads(r.read().decode()).get("status") == "ready":
                        window.load_url(url); return
            except Exception:
                time.sleep(0.15)
        try: window.load_url(url)
        except Exception: pass
    threading.Thread(target=_nav, daemon=True).start()
    if ico: webview.start(icon=ico)
    else: webview.start()

if __name__ == "__main__": main()
