"""告警领域：规则引擎（订阅事件总线 price.recorded / stock.changed）与通知渠道。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'on_event': 'app.api.lifecycle', 'send': 'app.api.lifecycle', 'CHANNEL_TYPES': 'app.api.lifecycle'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
