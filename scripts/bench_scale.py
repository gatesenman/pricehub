"""容量压测：10 万 SKU / 100 万价格点。用法：python scripts/bench_scale.py [--backend sqlite|postgresql] [--skus 100000] [--per 10]
测：批量写入吞吐、常用查询延迟（P50/P95）、分页接口、FAISS 向量检索（10 万 × 384 维）。结果写 docs/bench/<backend>.json。"""
import os, sys, time, json, random, argparse, tempfile, statistics, datetime
ap = argparse.ArgumentParser(); ap.add_argument("--backend", default="sqlite"); ap.add_argument("--skus", type=int, default=100_000); ap.add_argument("--per", type=int, default=10)
ap.add_argument("--pg", default="postgresql://pricehub:pricehub@127.0.0.1:5432/pricehub_test"); args = ap.parse_args()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ["PRICEHUB_DB"] = tempfile.mktemp(suffix=".bench.db")
if args.backend == "postgresql": os.environ.update(PRICEHUB_DB_URL=args.pg, PRICEHUB_PG_SCHEMA="bench_" + str(int(time.time())))
from app.core import storage as db
import numpy as np
db.init(); R = {"backend": args.backend, "skus": args.skus, "price_points": args.skus * args.per, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " UTC+8", "cpu": os.cpu_count()}
rnd = random.Random(1); PLAT = [f"shop{i}.example" for i in range(200)]; CAT = ["手机", "耳机", "电脑", "相机", "家电", "服饰", "美妆", "食品", "图书", "玩具"]
def timeit(fn, n=200):
    ts = []
    for _ in range(n): t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1000)
    ts.sort(); return {"p50_ms": round(ts[len(ts) // 2], 3), "p95_ms": round(ts[int(len(ts) * .95) - 1], 3), "n": n}
t0 = time.perf_counter(); B = 10_000
for s in range(0, args.skus, B):
    db.ex_many("INSERT INTO products(platform,external_id,title,category,brand,group_key,currency) VALUES(?,?,?,?,?,?,?)",
               [(PLAT[i % 200], f"E{i}", f"测试商品 {i} {CAT[i % 10]} 型号{i % 977}", CAT[i % 10], f"品牌{i % 300}", f"g{i % 20000}", "CNY") for i in range(s, min(s + B, args.skus))])
R["insert_products_s"] = round(time.perf_counter() - t0, 2)
t0 = time.perf_counter(); base = datetime.datetime(2026, 1, 1)
rows = []
for d in range(args.per):
    ts = (base + datetime.timedelta(days=d * 7)).strftime("%Y-%m-%d %H:%M:%S")
    for pid in range(1, args.skus + 1):
        rows.append((pid, round(100 + (pid % 5000) * 0.37 + rnd.uniform(-5, 5), 2), ts, "bench"))
        if len(rows) >= 50_000: db.ex_many("INSERT INTO prices(product_id,price,ts,method) VALUES(?,?,?,?)", rows); rows = []
if rows: db.ex_many("INSERT INTO prices(product_id,price,ts,method) VALUES(?,?,?,?)", rows)
el = time.perf_counter() - t0; R["insert_prices_s"] = round(el, 2); R["insert_prices_per_s"] = int(args.skus * args.per / el)
pick = lambda: rnd.randint(1, args.skus)
Q = {
  "单品最新价": lambda: db.q("SELECT price FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (pick(),), one=True),
  "单品历史(全部点)": lambda: db.q("SELECT price,ts FROM prices WHERE product_id=? ORDER BY id", (pick(),)),
  "同款组比价": lambda: db.q("SELECT id FROM products WHERE group_key=?", (f"g{rnd.randint(0, 19999)}",)),
  "按平台分页(50条)": lambda: db.q("SELECT id,title FROM products WHERE platform=? ORDER BY id LIMIT 50 OFFSET 100", (PLAT[rnd.randint(0, 199)],)),
}
R["queries"] = {k: timeit(f) for k, f in Q.items()}
R["queries"]["标题模糊搜索(LIKE)"] = timeit(lambda: db.q("SELECT id FROM products WHERE title LIKE ? LIMIT 20", (f"%型号{rnd.randint(0, 976)}%",)), 30)
R["queries"]["全量最新价聚合(100万点)"] = timeit(lambda: db.q("SELECT COUNT(*) n FROM prices WHERE id IN (SELECT MAX(id) FROM prices GROUP BY product_id)"), 3)
R["queries"]["按类目统计均价"] = timeit(lambda: db.q("SELECT p.category, AVG(x.price) a FROM products p JOIN prices x ON x.product_id=p.id GROUP BY p.category"), 3)
# 分页接口（与 /api/catalog/page 同逻辑）
os.environ.setdefault("PRICEHUB_NO_ST", "1")
from app.api import platform as P
R["queries"]["分页接口 /api/catalog/page"] = timeit(lambda: P.catalog_page(offset=rnd.randint(0, args.skus - 100), limit=50), 50)
# 向量检索：10 万 × 384 维（与多语言文本模型同维度）
from app.core.vector import VectorIndex
X = np.random.default_rng(0).normal(size=(args.skus, 384)).astype("float32"); X /= np.linalg.norm(X, axis=1, keepdims=True)
qs = X[np.random.default_rng(1).integers(0, args.skus, 200)]
V = {}
for kind in ("flat", "hnsw"):
    t = time.perf_counter(); vi = VectorIndex(kind=kind).build(X); bt = time.perf_counter() - t
    hits, lat = 0, []
    for i, q in enumerate(qs):
        t = time.perf_counter(); I, _ = vi.search(q, 10); lat.append((time.perf_counter() - t) * 1000); hits += int(np.argmax(X @ q) == I[0])
    lat.sort(); V[vi.engine] = {"build_s": round(bt, 2), "p50_ms": round(lat[100], 3), "p95_ms": round(lat[189], 3), "top1_recall": hits / len(qs)}
lat = []
for q in qs[:50]:
    t = time.perf_counter(); s = X @ q; np.argpartition(-s, 10)[:10]; lat.append((time.perf_counter() - t) * 1000)
lat.sort(); V["numpy 暴力(对照)"] = {"p50_ms": round(lat[25], 3), "p95_ms": round(lat[47], 3)}
R["vector_100k_384d"] = V
if args.backend == "sqlite": R["db_file_mb"] = round(os.path.getsize(os.environ["PRICEHUB_DB"]) / 1e6, 1); os.remove(os.environ["PRICEHUB_DB"])
else:
    R["db_size_mb"] = round(db.q("SELECT pg_database_size(current_database()) s", one=True)["s"] / 1e6, 1)
    db.pg_conn().execute(f'DROP SCHEMA "{db._pg_schema()}" CASCADE'); db.pg_conn().commit()
os.makedirs(os.path.join(ROOT, "docs", "bench"), exist_ok=True)
json.dump(R, open(os.path.join(ROOT, "docs", "bench", f"{args.backend}.json"), "w"), ensure_ascii=False, indent=1); print(json.dumps(R, ensure_ascii=False, indent=1))
