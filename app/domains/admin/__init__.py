"""管理领域：令牌与角色、审计、备份恢复、设置。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现已下沉到本包（v7）。"""
import importlib
_EXPORTS = {'tokens': 'app.domains.admin.service', 'audit': 'app.domains.admin.service', 'backup': 'app.domains.admin.service', 'get_setting': 'app.api.experience', 'set_setting': 'app.api.experience'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
