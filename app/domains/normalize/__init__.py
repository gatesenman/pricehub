"""归一领域：币种汇率、到手价、置信度。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'fx': 'app.api.lifecycle', 'to_cny': 'app.api.lifecycle', 'landed': 'app.api.lifecycle', 'confidence': 'app.domains.normalize.confidence:*'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
