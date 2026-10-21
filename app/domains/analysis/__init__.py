"""分析领域：统计、预测、买入建议、促销日历、季节性、卖家画像。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现已下沉到本包（v7）。"""
import importlib
_EXPORTS = {'analytics': 'app.domains.analysis.analytics:*', 'buy_advice': 'app.domains.analysis.advice', 'promo_events': 'app.domains.analysis.advice', 'seasonality': 'app.domains.analysis.advice', 'sellers': 'app.domains.analysis.advice'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
