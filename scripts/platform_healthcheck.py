"""批量体检全部内置平台：python scripts/platform_healthcheck.py [--concurrency 40] [--rps 20] [--limit N]"""
import argparse, asyncio, json, os, sys, time, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); warnings.filterwarnings("ignore")
os.environ.setdefault("PRICEHUB_NO_ST", "1"); os.environ.setdefault("PRICEHUB_NO_CLIP", "1")
from app import db, healthcheck, main  # main 导入时完成建表与平台入库
ap = argparse.ArgumentParser(); ap.add_argument("--concurrency", type=int, default=40); ap.add_argument("--rps", type=float, default=20); ap.add_argument("--limit", type=int); ap.add_argument("--timeout", type=float, default=12)
a = ap.parse_args(); t = time.time()
n = asyncio.run(healthcheck.run(a.concurrency, a.rps, a.timeout, a.limit))
st = healthcheck.stats(); print(f"体检完成：{n} 个唯一域名，用时 {time.time()-t:.0f}s"); print(json.dumps(st, ensure_ascii=False))
json.dump({"domains": n, "seconds": round(time.time() - t), "stats": st}, open(os.path.join(os.path.dirname(db.DB_PATH), "healthcheck_summary.json"), "w"), ensure_ascii=False)
