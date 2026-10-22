"""v9 预测回测 API。"""
from app.main import app
from app import db
from app.domains.analysis.backtest import backtest_with_prophet
@app.get("/api/products/{pid}/backtest")
def product_backtest(pid: int, horizon: int = 7, folds: int = 4, sync: bool = False):
    h = db.q("SELECT price, ts FROM prices WHERE product_id=? ORDER BY ts, id", (pid,))
    return backtest_with_prophet(h, max(1, min(horizon, 30)), max(1, min(folds, 8)), sync=sync)
