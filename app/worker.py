"""独立采集 worker 进程：python -m app.worker [--concurrency 4]
从 Redis Streams 队列取任务执行（需 PRICEHUB_QUEUE=redis）。与 API 进程共用数据库与插件目录，可多机多进程水平扩展。"""
import os, sys, time, argparse, signal, logging
ap = argparse.ArgumentParser(); ap.add_argument("--concurrency", type=int, default=int(os.environ.get("PRICEHUB_WORKERS", "4"))); a = ap.parse_args()
os.environ["PRICEHUB_QUEUE"] = os.environ.get("PRICEHUB_QUEUE", "redis"); os.environ["PRICEHUB_EMBEDDED_WORKER"] = "1"; os.environ["PRICEHUB_WORKERS"] = str(a.concurrency)
os.environ.setdefault("PRICEHUB_NO_ST", "1"); os.environ.setdefault("PRICEHUB_NO_CLIP", "1")   # worker 不需要搜索模型
from app import main  # noqa: E402  加载数据源、插件、任务处理函数
from app.api.platform import SCHED  # noqa: E402
log = logging.getLogger("pricehub.worker")
if getattr(SCHED, "backend", "") != "redis": sys.exit("worker 需要 Redis 队列：请设置 PRICEHUB_QUEUE=redis 并确认 PRICEHUB_REDIS_URL 可连通")
SCHED.start(); log.info("worker started", extra={"consumer": SCHED.consumer, "concurrency": a.concurrency}); print(f"PriceHub worker 已启动：{SCHED.consumer}，并发 {a.concurrency}", flush=True)
stop = []
signal.signal(signal.SIGTERM, lambda *x: stop.append(1)); signal.signal(signal.SIGINT, lambda *x: stop.append(1))
while not stop: time.sleep(0.5)
SCHED.stop()
