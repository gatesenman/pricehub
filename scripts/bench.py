"""性能基准：临时库灌入 N 个商品（每个 10 条历史价），测接口、语义搜索与前端渲染耗时。
用法：python scripts/bench.py <项目目录> <端口> [N]"""
import os, sys, time, json, random, subprocess, tempfile, sqlite3, statistics, httpx
root, port = sys.argv[1], int(sys.argv[2]); N = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
dbp = tempfile.mktemp(suffix=".db"); env = dict(os.environ, PRICEHUB_DB=dbp, PRICEHUB_NO_CLIP="1", PRICEHUB_ONBOARDED="1", PRICEHUB_EMB_CACHE=dbp + ".npz")
U = f"http://127.0.0.1:{port}"
def start():
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try: httpx.get(U + "/api/health", timeout=2); return p
        except Exception: time.sleep(0.5)
p = start(); p.terminate(); p.wait()
con = sqlite3.connect(dbp); words = ["手机", "耳机", "Headphones", "ゲーム機", "键盘", "Keyboard", "绿茶", "Tea", "显示器", "Monitor", "充电器", "Charger", "运动鞋", "Sneakers", "咖啡机", "Coffee"]
rnd = random.Random(1)
for i in range(N):
    w = rnd.choice(words); con.execute("INSERT INTO products(platform,external_id,title,category,chain,brand,currency,group_key,attrs) VALUES(?,?,?,?,?,?,?,?,?)",
        (f"shop{i%40}.com", str(i), f"{w} 型号{i} {rnd.choice(['Pro','Max','Lite','标准版'])}", w, "消费电子>" + w, "Brand" + str(i % 50), "CNY", f"g{i//3}", "{}"))
    for d in range(10): con.execute("INSERT INTO prices(product_id,price,ts,confidence,conf_level,conf_basis,method) VALUES(?,?,?,?,?,?,?)", (i + 1, 100 + rnd.random() * 50, f"2026-10-{d+1:02d} 00:00:00", 0.9, "高", "[]", "mock"))
con.commit(); con.close()
p = start(); c = httpx.Client(base_url=U, timeout=600); res = {}
def t(name, fn, n=5):
    ts = []
    for _ in range(n): a = time.perf_counter(); fn(); ts.append((time.perf_counter() - a) * 1000)
    res[name] = round(statistics.median(ts), 1)
Q = ["降噪耳机", "game console", "green tea", "キーボード", "运动鞋"]
t("语义搜索-首次(冷)", lambda: c.get("/api/search/semantic", params={"q": "降噪耳机"}), 1)
t("语义搜索-热(中位数)", lambda: c.get("/api/search/semantic", params={"q": rnd.choice(Q)}))
t("商品列表 /api/products", lambda: c.get("/api/products"))
t("仪表盘 /api/dashboard", lambda: c.get("/api/dashboard"))
t("平台目录全量 920 条", lambda: c.get("/api/platforms", params={"limit": 2000}))
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
    a = time.perf_counter(); pg.goto(U + "/"); pg.wait_for_function("document.querySelector('[data-stat]') && document.querySelector('[data-stat]').innerText.trim()!=='—'", timeout=120000); res["首屏可交互(ms)"] = round((time.perf_counter() - a) * 1000)
    a = time.perf_counter(); pg.click("[data-tb='平台目录']"); pg.wait_for_selector("[data-view=catalog] [data-cat-row]", timeout=60000); res["打开平台目录(ms)"] = round((time.perf_counter() - a) * 1000)
    if pg.locator("[data-view=catalog] [data-cat-all]").count(): pg.click("[data-view=catalog] [data-cat-all]"); pg.wait_for_timeout(100)
    res["平台目录DOM行数"] = pg.locator("[data-view=catalog] [data-cat-row]").count()
    a = time.perf_counter(); pg.locator("[data-tab=t2]").click(); pg.wait_for_selector("[data-view=products] [data-row]")
    if pg.locator("[data-view=products] [data-per]").count(): pg.select_option("[data-view=products] [data-per]", "100")
    pg.wait_for_timeout(30); res["商品表渲染(ms)"] = round((time.perf_counter() - a) * 1000); res["商品表DOM行数"] = pg.locator("[data-view=products] [data-row]").count()
    b.close()
p.terminate(); print(json.dumps(res, ensure_ascii=False, indent=1))
