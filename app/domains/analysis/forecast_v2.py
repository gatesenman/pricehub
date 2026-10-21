"""v8 智能预测：在线性回归/移动平均/指数平滑之上叠加季节性分解、价格弹性粗估、买入时机概率与置信区间。

模型优先级：Prophet（有足够历史）→ statsmodels STL + ARIMA → 仅线性+移动平均回退。
促销日历（双11/618/黑五等）作为外生事件强度，进入买入概率打分。
"""
from __future__ import annotations
import datetime, math, logging
import numpy as np
log = logging.getLogger("pricehub.forecast")
HAVE_PROPHET = HAVE_SM = False
try:
    from prophet import Prophet; HAVE_PROPHET = True
except Exception as e: log.info("prophet unavailable", extra={"error": str(e)})
try:
    from statsmodels.tsa.seasonal import STL
    from statsmodels.tsa.arima.model import ARIMA
    HAVE_SM = True
except Exception as e: log.info("statsmodels unavailable", extra={"error": str(e)})

def _series(prices):
    """prices: [{price, ts?}, ...] → (y float64, dates date|None)"""
    y, dates = [], []
    for i, x in enumerate(prices):
        try: y.append(float(x["price"] if isinstance(x, dict) else x))
        except (TypeError, ValueError): continue
        d = None
        if isinstance(x, dict) and x.get("ts"):
            try: d = datetime.datetime.strptime(str(x["ts"])[:10], "%Y-%m-%d").date()
            except Exception: d = None
        dates.append(d or (datetime.date.today() - datetime.timedelta(days=len(prices) - i - 1)))
    return np.asarray(y, float), dates

def _ci_from_resid(resid, point, z=1.645):
    """用残差标准差估 90% 置信区间。"""
    s = float(np.std(resid)) if len(resid) else 0.0
    if s <= 0: s = abs(point) * 0.03 or 1.0
    return round(point - z * s, 2), round(point + z * s, 2), round(s, 4)

def _baseline(y, horizon):
    x = np.arange(len(y)); k, b = np.polyfit(x, y, 1)
    lin = [round(float(k * (len(y) + i) + b), 2) for i in range(horizon)]
    ma = round(float(y[-min(3, len(y)):].mean()), 2)
    alpha, s = 0.5, float(y[0])
    for v in y[1:]: s = alpha * v + (1 - alpha) * s
    resid = y - (k * x + b)
    lo, hi, sigma = _ci_from_resid(resid, lin[-1] if lin else ma)
    trend = "上涨" if k > 0.005 * y.mean() else ("下跌" if k < -0.005 * y.mean() else "平稳")
    return {"linear_regression": lin, "moving_average": ma, "exp_smoothing": round(s, 2), "slope": round(float(k), 4),
            "trend": trend, "point": lin[-1] if lin else ma, "ci90": [lo, hi], "sigma": sigma, "engine": "baseline"}

def _promo_intensity(dates, horizon, promo_fn):
    """未来 horizon 天内促销强度 0..1（距离越近、预热越长越高）。"""
    today = dates[-1] if dates else datetime.date.today()
    events = promo_fn(max(horizon + 60, 90)) if promo_fn else []
    out = []
    for i in range(horizon):
        d = today + datetime.timedelta(days=i + 1); best = 0.0
        for e in events:
            try: ed = datetime.date.fromisoformat(e["date"])
            except Exception: continue
            warm = int(e.get("warmup_days") or 3); left = (ed - d).days
            if -1 <= left <= warm: best = max(best, 1.0 - abs(left) / max(warm, 1) * 0.5)
        out.append(round(best, 3))
    return out

def _prophet(y, dates, horizon):
    import pandas as pd
    df = pd.DataFrame({"ds": [datetime.datetime.combine(d, datetime.time()) for d in dates], "y": y})
    m = Prophet(yearly_seasonality=len(y) >= 120, weekly_seasonality=len(y) >= 14, daily_seasonality=False,
                interval_width=0.9, changepoint_prior_scale=0.05)
    m.fit(df); fut = m.make_future_dataframe(periods=horizon)
    fc = m.predict(fut).tail(horizon)
    pts = [round(float(v), 2) for v in fc["yhat"]]
    return {"points": pts, "ci_low": [round(float(v), 2) for v in fc["yhat_lower"]],
            "ci_high": [round(float(v), 2) for v in fc["yhat_upper"]], "engine": "prophet",
            "point": pts[-1], "ci90": [round(float(fc["yhat_lower"].iloc[-1]), 2), round(float(fc["yhat_upper"].iloc[-1]), 2)]}

def _stl_arima(y, horizon):
    period = 7 if len(y) >= 21 else max(2, len(y) // 3)
    stl = STL(y, period=period, robust=True).fit()
    seasonal_last = float(stl.seasonal[-period:].mean()) if period else 0.0
    resid = stl.resid
    try:
        fit = ARIMA(stl.trend[~np.isnan(stl.trend)], order=(1, 1, 1)).fit()
        trend_fc = fit.forecast(horizon)
    except Exception:
        x = np.arange(len(y)); k, b = np.polyfit(x, y, 1)
        trend_fc = [k * (len(y) + i) + b for i in range(horizon)]
    pts = [round(float(t + seasonal_last), 2) for t in trend_fc]
    lo, hi, sigma = _ci_from_resid(np.asarray(resid[~np.isnan(resid)]), pts[-1])
    return {"points": pts, "ci_low": [round(p - 1.645 * sigma, 2) for p in pts], "ci_high": [round(p + 1.645 * sigma, 2) for p in pts],
            "point": pts[-1], "ci90": [lo, hi], "sigma": sigma, "engine": "stl+arima",
            "seasonal_strength": round(float(1 - np.var(resid[~np.isnan(resid)]) / max(np.var(y), 1e-9)), 3)}

def elasticity_estimate(y, dates=None):
    """价格弹性粗估：用相邻价差对应的“相对销量代理”（价降→需求升的对数斜率）。
    无真实销量时，用价格变化幅度的反号作为需求代理，仅作方向性参考。"""
    if len(y) < 8: return {"ok": False, "reason": "历史少于 8 条，无法估弹性"}
    # 把价格序列切成约等长窗口，用窗口均价 vs 窗口内“活跃度”（价格波动次数的倒数）做 log-log 回归
    w = max(3, len(y) // 6); xs, ys = [], []
    for i in range(0, len(y) - w + 1, w):
        seg = y[i:i + w]; avg = float(seg.mean())
        if avg <= 0: continue
        vol = float(np.std(seg) / avg) + 0.01   # 波动大 ≈ 促销活跃 ≈ 需求高的代理
        demand_proxy = 1.0 / vol
        xs.append(math.log(avg)); ys.append(math.log(demand_proxy))
    if len(xs) < 3: return {"ok": False, "reason": "有效窗口不足"}
    X, Y = np.asarray(xs), np.asarray(ys)
    k = float(np.polyfit(X, Y, 1)[0])   # d ln Q / d ln P
    # 夹到常见零售弹性区间
    k = max(-3.0, min(-0.1, k)) if k < 0 else -max(0.1, min(1.0, abs(k) or 0.3))
    label = "高弹性（降价能明显拉动）" if k <= -1.2 else ("中弹性" if k <= -0.6 else "低弹性（价格不敏感）")
    return {"ok": True, "elasticity": round(k, 3), "label": label,
            "note": "基于价格波动代理估算，非真实销量；仅作方向参考。降价 1% 约对应需求变化 " + f"{-k:.1f}%（粗估）。"}

def buy_probability(y, seasonal_fc, promos, elasticity=None):
    """买入时机概率 0..1：历史分位 + 预测方向 + 促销距离 + 弹性。"""
    cur = float(y[-1]); pct = float((y < cur).mean())   # 现价高于多少历史
    # 基础：现价越接近历史低位，越该买
    p = 0.55 - (pct - 0.5) * 0.7
    # 预测：未来点低于现价 → 再等等；高于 → 现在买
    fut = seasonal_fc.get("point") or seasonal_fc.get("moving_average")
    if fut is not None and cur:
        delta = (fut - cur) / cur
        p += 0.15 if delta > 0.02 else (-0.15 if delta < -0.02 else 0)
    # 促销：临近大促 → 降概率；正在大促 → 升
    near = [e for e in (promos or []) if e.get("days_left", 99) <= 30]
    if near:
        e0 = near[0]
        if e0.get("in_progress"): p += 0.12
        elif e0["days_left"] <= 14: p -= 0.18
        else: p -= 0.08
    if elasticity and elasticity.get("ok") and elasticity["elasticity"] <= -1.0 and near and not near[0].get("in_progress"):
        p -= 0.05   # 高弹性商品更值得等到大促
    p = max(0.05, min(0.95, p))
    # 置信：样本多、残差小 → 置信高
    sigma = seasonal_fc.get("sigma") or (abs(cur) * 0.05)
    conf = max(0.25, min(0.92, 0.35 + 0.01 * min(len(y), 40) - min(0.3, float(sigma) / max(cur, 1) * 2)))
    band = 1.645 * math.sqrt(p * (1 - p) / max(len(y), 5))   # 概率本身的粗置信带
    return {"probability": round(p, 3), "ci90": [round(max(0, p - band), 3), round(min(1, p + band), 3)],
            "confidence": round(conf, 3),
            "label": "建议现在买" if p >= 0.65 else ("可以考虑买" if p >= 0.5 else ("建议再等等" if p <= 0.35 else "观望"))}

def forecast_advanced(prices, horizon=7, promo_fn=None):
    """主入口。返回兼容旧字段 + 新字段（seasonal / elasticity / buy / promo_intensity）。"""
    y, dates = _series(prices)
    if len(y) < 2: return {"error": "历史数据不足(>=2)"}
    base = _baseline(y, horizon)
    seasonal = None; engine_try = []
    if HAVE_PROPHET and len(y) >= 16:
        try: seasonal = _prophet(y, dates, horizon); engine_try.append("prophet")
        except Exception as e: log.warning("prophet failed", extra={"error": str(e)}); engine_try.append(f"prophet:{e}")
    if seasonal is None and HAVE_SM and len(y) >= 14:
        try: seasonal = _stl_arima(y, horizon); engine_try.append("stl+arima")
        except Exception as e: log.warning("stl+arima failed", extra={"error": str(e)}); engine_try.append(f"stl:{e}")
    if seasonal is None:
        seasonal = {"points": base["linear_regression"], "point": base["point"], "ci90": base["ci90"], "ci_low": [base["ci90"][0]] * horizon,
                    "ci_high": [base["ci90"][1]] * horizon, "engine": "baseline", "sigma": base["sigma"]}
        engine_try.append("baseline")
    el = elasticity_estimate(y, dates)
    # 促销强度曲线
    if promo_fn is None:
        from app.domains.analysis.advice import promo_calendar as promo_fn   # 延迟导入，避免与 advice 循环
    intensity = _promo_intensity(dates, horizon, promo_fn)
    promos = [e for e in promo_fn(45) if e["days_left"] >= -e["warmup_days"]]
    buy = buy_probability(y, seasonal, promos, el)
    out = dict(base)
    out.update({
        "horizon": horizon, "n": len(y),
        "seasonal": seasonal, "seasonal_points": seasonal.get("points"),
        "seasonal_ci": {"low": seasonal.get("ci_low"), "high": seasonal.get("ci_high")},
        "engines_tried": engine_try, "engine": seasonal.get("engine"),
        "elasticity": el, "buy": buy, "promo_intensity": intensity,
        "have_prophet": HAVE_PROPHET, "have_statsmodels": HAVE_SM,
        "point_forecast": seasonal.get("point"), "ci90": seasonal.get("ci90") or base["ci90"],
    })
    return out
