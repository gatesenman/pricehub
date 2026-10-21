"""可观测性：Prometheus 指标、结构化 JSON 日志、健康检查。"""
import json, logging, time, os, sys
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
REGISTRY = CollectorRegistry()
HTTP_REQ = Counter("pricehub_http_requests_total", "HTTP 请求数", ["method", "route", "status"], registry=REGISTRY)
HTTP_LAT = Histogram("pricehub_http_request_seconds", "HTTP 请求耗时", ["route"], registry=REGISTRY, buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
COLLECT = Counter("pricehub_collect_total", "采集结果", ["result", "method"], registry=REGISTRY)
EVENTS = Counter("pricehub_events_total", "事件总线发布数", ["topic"], registry=REGISTRY)
NOTIFY = Counter("pricehub_notifications_total", "通知发送", ["channel_type", "ok"], registry=REGISTRY)
JOBS = Counter("pricehub_jobs_total", "调度任务结果", ["kind", "result"], registry=REGISTRY)
QUEUE = Gauge("pricehub_job_queue_depth", "调度队列长度", registry=REGISTRY)
DEAD = Gauge("pricehub_dead_letters", "死信数量", registry=REGISTRY)
PRODUCTS = Gauge("pricehub_products", "商品数", registry=REGISTRY)
PRICES = Gauge("pricehub_price_points", "价格点数", registry=REGISTRY)
UP = Gauge("pricehub_up", "服务存活", registry=REGISTRY); UP.set(1)
START = time.time()
def metrics_text(): return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
class JsonFormatter(logging.Formatter):
    STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
        d.update({k: v for k, v in vars(r).items() if k not in self.STD and not k.startswith("_")})
        if r.exc_info: d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)
def setup_logging():
    lg = logging.getLogger("pricehub")
    if lg.handlers: return lg
    h = logging.StreamHandler(sys.stderr) if os.environ.get("PRICEHUB_LOG_FILE") is None else logging.FileHandler(os.environ["PRICEHUB_LOG_FILE"], encoding="utf-8")
    h.setFormatter(JsonFormatter()); lg.addHandler(h); lg.setLevel(os.environ.get("PRICEHUB_LOG_LEVEL", "INFO")); lg.propagate = False
    return lg
