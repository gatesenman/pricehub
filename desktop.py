"""PriceHub 桌面窗口启动器（pywebview）。
用法：
  python desktop.py                 # 内嵌模式：在本进程启动 FastAPI 服务(127.0.0.1:随机端口)并打开桌面窗口
  python desktop.py --url http://服务器:8800   # 瘦客户端模式：连接已运行的 PriceHub 服务
"""
import argparse, socket, threading, time, sys, urllib.request
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--url"); ap.add_argument("--port", type=int); a = ap.parse_args()
    url = a.url
    if not url:
        import uvicorn
        port = a.port or free_port(); url = f"http://127.0.0.1:{port}"
        threading.Thread(target=lambda: uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
        for _ in range(240):
            try: urllib.request.urlopen(url + "/api/health", timeout=1); break
            except Exception: time.sleep(0.5)
    import webview
    webview.create_window("PriceHub 价格管理工作台", url, width=1440, height=900, min_size=(1024, 640))
    webview.start()
if __name__ == "__main__": main()
