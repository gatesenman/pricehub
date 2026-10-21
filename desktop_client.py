"""瘦客户端入口（用于 PyInstaller 打包）：连接 PRICEHUB_URL（默认 http://127.0.0.1:8800）。"""
import os, sys, webview
url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PRICEHUB_URL", "http://127.0.0.1:8800")
webview.create_window("PriceHub 价格管理工作台", url, width=1440, height=900, min_size=(1024, 640)); webview.start()
