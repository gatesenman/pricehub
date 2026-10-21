"""集成领域：报表导出与推送、书签工具、Webhook、插件 SDK。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'report_data': 'app.api.lifecycle', 'report_text': 'app.api.lifecycle', 'sdk': 'app.sdk:*'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
