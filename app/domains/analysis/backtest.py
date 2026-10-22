"""v9 预测回测：滚动起点（rolling-origin）回测，比较各引擎的误差与 90% 区间覆盖率，
给出该商品“最可信的引擎”。完全离线，用商品自身历史，不需要第三方密钥。"""
from __future__ import annotations
import numpy as np
from app.domains.analysis import forecast_v2 as F

def _naive(y, h):
    return {"points": [float(y[-1])] * h, "ci_low": None, "ci_high": None}

def _base(y, h):
    b = F._baseline(y, h); s = b["sigma"]
    return {"points": b["linear_regression"], "ci_low": [p - 1.645 * s for p in b["linear_regression"]],
            "ci_high": [p + 1.645 * s for p in b["linear_regression"]]}

def _stl(y, h):
    if not F.HAVE_SM or len(y) < 14: return None
    r = F._stl_arima(y, h); return {"points": r["points"], "ci_low": r["ci_low"], "ci_high": r["ci_high"]}

ENGINES = {"naive": _naive, "baseline": _base, "stl+arima": _stl}

def backtest(prices, horizon=7, folds=4, min_train=14, engines=None):
    y, _ = F._series(prices)
    n = len(y)
    if n < min_train + horizon:
        return {"ok": False, "reason": f"历史数据不足：至少需要 {min_train + horizon} 条，当前 {n} 条", "n": n}
    folds = max(1, min(folds, (n - min_train) // horizon))
    origins = [n - horizon * (k + 1) for k in range(folds)][::-1]
    res = {}
    for name in (engines or ENGINES):
        fn = ENGINES[name]; errs, apes, cover, cov_n, used = [], [], 0, 0, 0
        for o in origins:
            try: fc = fn(y[:o], horizon)
            except Exception: fc = None
            if not fc: continue
            used += 1; actual = y[o:o + horizon]; pts = np.asarray(fc["points"][:len(actual)], float)
            e = actual - pts; errs.extend(np.abs(e).tolist())
            apes.extend((np.abs(e) / np.maximum(np.abs(actual), 1e-9)).tolist())
            if fc.get("ci_low") is not None:
                lo = np.asarray(fc["ci_low"][:len(actual)]); hi = np.asarray(fc["ci_high"][:len(actual)])
                cover += int(((actual >= lo) & (actual <= hi)).sum()); cov_n += len(actual)
        if not used: continue
        res[name] = {"folds": used, "mae": round(float(np.mean(errs)), 4), "mape": round(float(np.mean(apes)) * 100, 2),
                     "rmse": round(float(np.sqrt(np.mean(np.square(errs)))), 4),
                     "ci90_coverage": round(cover / cov_n * 100, 1) if cov_n else None}
    if not res: return {"ok": False, "reason": "所有引擎回测失败", "n": n}
    best = min(res, key=lambda k: res[k]["mape"])
    naive = res.get("naive", {}).get("mape")
    skill = round((1 - res[best]["mape"] / naive) * 100, 1) if naive else None
    b = res[best]["mape"]
    grade = "高" if b < 3 else ("中" if b < 8 else "低")
    note = f"最近 {folds} 个窗口、每次预测 {horizon} 天：{best} 平均误差 {b}%"
    if skill is not None: note += f"，比“维持现价”{'好' if skill > 0 else '差'} {abs(skill)}%"
    return {"ok": True, "n": n, "horizon": horizon, "folds": folds, "engines": res, "best": best,
            "skill_vs_naive_pct": skill, "reliability": grade, "note": note}
