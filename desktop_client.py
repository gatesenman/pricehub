"""瘦客户端入口（用于 PyInstaller 打包）：连接 PRICEHUB_URL（默认 http://127.0.0.1:8800）。"""
import os, sys, webview
url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PRICEHUB_URL", "http://127.0.0.1:8800")
def icon_path():
    root = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(root, "windows", "installer", "PriceHub.ico"),
        os.path.join(root, "app", "static", "icons", "pricehub.ico"),
        os.path.join(root, "PriceHub.ico"),
    ):
        if os.path.isfile(p): return p
    return None
ico = icon_path()
webview.create_window("PriceHub", url, width=1440, height=900, min_size=(1024, 640))
webview.start(icon=ico) if ico else webview.start()
