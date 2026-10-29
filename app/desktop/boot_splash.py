"""桌面冷启动闪屏 HTML（不依赖本地 HTTP 服务）。"""
BOOT_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>PriceHub</title>
<style>
html,body{margin:0;height:100%;background:#1e1e1e;color:#d4d4d4;font:13px/1.4 "Segoe UI",system-ui,"PingFang SC",sans-serif}
.wrap{height:100%;display:grid;place-items:center}
.card{text-align:center;padding:32px 40px;border-radius:12px;background:#252526;border:1px solid #3c3c3c;box-shadow:0 12px 40px #0008;min-width:260px}
.mark{width:64px;height:64px;margin:0 auto 12px;border-radius:14px;background:linear-gradient(145deg,#0e7ad1,#1f8ad2);display:grid;place-items:center;box-shadow:0 4px 14px #0005}
.mark svg{width:34px;height:34px}
h1{margin:0 0 6px;font-size:18px;color:#3794ff}
p{margin:0;color:#8b8b8b}
.bar{width:160px;height:3px;border-radius:2px;background:#2d2d30;overflow:hidden;margin:14px auto 0}
.bar>i{display:block;height:100%;width:40%;background:#0e7ad1;border-radius:2px;animation:b 1.1s ease-in-out infinite}
@keyframes b{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}
</style></head><body><div class="wrap"><div class="card">
<div class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></div>
<h1>PriceHub</h1><p id="msg">正在启动服务…</p><div class="bar"><i></i></div>
</div></div>
<script>
let n=0; const msgs=['正在启动服务…','加载数据引擎…','准备桌面窗口…','即将进入工作台…'];
setInterval(()=>{ n=(n+1)%msgs.length; document.getElementById('msg').textContent=msgs[n]; }, 1200);
</script></body></html>
"""
