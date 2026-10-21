"""用量计量与配额：SKU 数（实时统计）、采集次数（按月）、API 调用（按天）。计数先在内存累加，每 5 秒或读取时批量落库。"""
import threading, time, datetime, json, collections
from app import db
from app.core import tenancy
PLANS = {"desktop": {"max_skus": None, "collects_per_month": None, "api_calls_per_day": None, "label": "单机版（不限）"},
         "free": {"max_skus": 500, "collects_per_month": 3000, "api_calls_per_day": 20000, "label": "免费版"},
         "team": {"max_skus": 20000, "collects_per_month": 100000, "api_calls_per_day": 500000, "label": "团队版"},
         "enterprise": {"max_skus": None, "collects_per_month": None, "api_calls_per_day": None, "label": "企业版（不限）"}}
METRICS = {"collects": ("采集次数", "month"), "api_calls": ("API 调用", "day")}
class QuotaExceeded(Exception): pass
_BUF = collections.Counter(); _LOCK = threading.Lock(); _LAST = [time.time()]
def period(kind, now=None):
    d = now or datetime.datetime.utcnow() + datetime.timedelta(hours=8)   # 按北京时间切日/月
    return d.strftime("%Y-%m-%d") if kind == "day" else d.strftime("%Y-%m")
def inc(metric, n=1, ws=None):
    ws = ws or tenancy.current()
    with _LOCK: _BUF[(ws, metric, period(METRICS[metric][1]))] += n
    if time.time() - _LAST[0] > 5: flush()
def flush():
    with _LOCK:
        items = list(_BUF.items()); _BUF.clear(); _LAST[0] = time.time()
    with tenancy.bypass():
        for (ws, m, p), n in items:
            db.ex("INSERT INTO usage(ws_id,metric,period,n) VALUES(?,?,?,?) ON CONFLICT(ws_id,metric,period) DO UPDATE SET n=usage.n+excluded.n", (ws, m, p, n))
def quota(ws=None):
    ws = ws or tenancy.current()
    with tenancy.bypass(): w = db.q("SELECT plan, quota FROM workspaces WHERE id=?", (ws,), one=True) or {"plan": "desktop", "quota": "{}"}
    q = dict(PLANS.get(w["plan"], PLANS["free"])); q.update({k: v for k, v in json.loads(w.get("quota") or "{}").items() if k in q}); q["plan"] = w["plan"]; return q
def used(metric, ws=None):
    ws = ws or tenancy.current(); flush()
    if metric == "skus":
        with tenancy.use(ws): return db.q("SELECT COUNT(*) n FROM products", one=True)["n"]
    with tenancy.bypass():
        r = db.q("SELECT n FROM usage WHERE ws_id=? AND metric=? AND period=?", (ws, metric, period(METRICS[metric][1])), one=True)
    return (r or {}).get("n", 0)
def check(metric, extra=1, ws=None):
    """超出配额时抛 QuotaExceeded。metric：skus / collects / api_calls。"""
    q = quota(ws); lim = q.get({"skus": "max_skus", "collects": "collects_per_month", "api_calls": "api_calls_per_day"}[metric])
    if lim is None: return
    u = used(metric, ws) + (_BUF_pending(metric, ws) if metric == "api_calls" else 0)
    if u + extra > lim:
        name = {"skus": "商品数", "collects": "本月采集次数", "api_calls": "今日 API 调用"}[metric]
        raise QuotaExceeded(f"已达到工作区{name}上限（{lim}），请升级套餐或联系管理员调整配额")
def _BUF_pending(metric, ws):
    ws = ws or tenancy.current()
    with _LOCK: return sum(n for (w, m, _), n in _BUF.items() if w == ws and m == metric)
def report(ws=None):
    ws = ws or tenancy.current(); q = quota(ws); flush()
    with tenancy.bypass(): hist = db.q("SELECT metric, period, n FROM usage WHERE ws_id=? ORDER BY period DESC LIMIT 120", (ws,))
    return {"plan": q["plan"], "plan_label": PLANS.get(q["plan"], {}).get("label", q["plan"]), "plans": {k: v["label"] for k, v in PLANS.items()},
            "items": [{"metric": "skus", "label": "商品数（SKU）", "used": used("skus", ws), "limit": q["max_skus"], "period": "当前"},
                      {"metric": "collects", "label": "本月采集次数", "used": used("collects", ws), "limit": q["collects_per_month"], "period": period("month")},
                      {"metric": "api_calls", "label": "今日 API 调用", "used": used("api_calls", ws), "limit": q["api_calls_per_day"], "period": period("day")}],
            "history": hist}
