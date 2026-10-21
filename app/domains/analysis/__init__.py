"""分析领域：统计、预测、买入建议、促销日历、季节性、卖家画像。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'analytics': 'app.domains.analysis.analytics:*', 'buy_advice': 'app.api.lifecycle', 'promo_events': 'app.api.lifecycle', 'seasonality': 'app.api.lifecycle', 'sellers': 'app.api.lifecycle'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
