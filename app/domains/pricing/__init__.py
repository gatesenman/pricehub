"""定价领域：定价策略、利润模拟、调价推送；控价（MAP、违规、证据）。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'apply_rule': 'app.api.lifecycle', 'simulate': 'app.api.lifecycle', 'map_violations': 'app.api.lifecycle', 'capture_evidence': 'app.api.lifecycle'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
