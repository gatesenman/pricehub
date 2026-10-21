"""搜索领域：多语言语义搜索（FAISS 向量索引）、以图搜图、AI 查询理解。

领域对外接口（延迟导入，避免与 API 层循环依赖）。业务实现逐步从 app.api.* 下沉到本包。"""
import importlib
_EXPORTS = {'semantic': 'app.domains.search.semantic:*', 'ai_query': 'app.domains.search.ai_query:*'}
def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    mod, _, star = _EXPORTS[name].partition(":")
    m = importlib.import_module(mod)
    return m if star else getattr(m, name)
__all__ = list(_EXPORTS)
