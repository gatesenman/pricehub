"""匹配领域：同款建议与合并。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现已下沉到本包（v7）。"""
import importlib
_EXPORTS = {'match_suggest': 'app.domains.match.service', 'match_merge': 'app.domains.match.service'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
