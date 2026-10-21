"""兼容垫片：实现已迁移到 app.api.lifecycle（v6 领域化重构）。保留旧导入路径，两者是同一个模块对象。"""
import sys, importlib
sys.modules[__name__] = importlib.import_module("app.api.lifecycle")
