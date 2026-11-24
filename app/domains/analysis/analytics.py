import numpy as np, re
def history_stats(prices):
    from app.core.price_input import observations
    p = np.array([value for _,_,value in observations(prices)], float)
    if not len(p): return {}
    return dict(count=len(p), min=float(p.min()), max=float(p.max()), avg=round(float(p.mean()), 2), last=float(p[-1]),
                change_pct=round(float((p[-1] - p[0]) / p[0] * 100), 2) if p[0] else 0)
def forecast(prices, horizon=7, window=3):
    """兼容入口：优先走 v8 高级预测（季节性/弹性/买入概率），失败时回退线性+移动平均+指数平滑。"""
    try:
        from .forecast_v2 import forecast_advanced
        return forecast_advanced(prices, horizon)
    except Exception:
        p = np.array([x["price"] if isinstance(x, dict) else x for x in prices], float)
        if len(p) < 2: return {"error": "历史数据不足(>=2)"}
        x = np.arange(len(p)); k, b = np.polyfit(x, p, 1)
        lin = [round(float(k * (len(p) + i) + b), 2) for i in range(horizon)]
        ma = round(float(p[-window:].mean()), 2)
        alpha, s = 0.5, p[0]
        for v in p[1:]: s = alpha * v + (1 - alpha) * s
        trend = "上涨" if k > 0.005 * p.mean() else "下跌" if k < -0.005 * p.mean() else "平稳"
        return dict(linear_regression=lin, moving_average=ma, exp_smoothing=round(float(s), 2), slope=round(float(k), 4), trend=trend)
def pricing(cost, competitor_prices, markup=0.2, strategy="auto"):
    out = {}
    if cost: out["cost_plus"] = round(cost * (1 + markup), 2)
    if competitor_prices:
        cp = sorted(competitor_prices)
        out["competitive_min_minus_1pct"] = round(cp[0] * 0.99, 2); out["market_median"] = float(np.median(cp))
    if cost and competitor_prices:
        floor = cost * 1.05
        out["recommended"] = round(max(floor, min(out["cost_plus"], out["competitive_min_minus_1pct"])), 2)
    else: out["recommended"] = out.get("cost_plus") or out.get("market_median")
    return out
def norm_key(title):
    t = re.sub(r"[^\w]+", " ", title.lower()); return " ".join(sorted(set(t.split())))
