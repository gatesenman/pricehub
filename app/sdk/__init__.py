"""PriceHub 数据源插件 SDK（版本 1.0）。

写一个插件只需三步：
1. 继承 SourcePlugin，填写 meta（id、名称、版本、配置项说明）；
2. 实现 fetch_items(ctx)，返回商品字典列表（字段见 ITEM_FIELDS）；
3. 把文件放进插件目录（默认 ./plugins，或环境变量 PRICEHUB_PLUGINS_DIR），启动或点“重新加载插件”即可使用。

生命周期：发现(discovered) → 加载(loaded) → 校验(validated) → 可用(active)；任一步失败为 error 并记录原因。
每次采集：validate_config → setup → fetch_items → 标准化/校验商品 → teardown（无论成功与否都会调用）。
"""
from __future__ import annotations
import importlib.util, os, sys, time, traceback, logging, hashlib
from dataclasses import dataclass, field, asdict
from .. import sources
SDK_VERSION = "1.0"
log = logging.getLogger("pricehub.sdk")
ITEM_FIELDS = {"external_id": "平台内唯一编号（必填）", "title": "商品标题（必填）", "price": "价格，数字（必填，没有价格可为 None）",
               "currency": "币种，默认 CNY", "url": "商品链接", "category": "类目", "brand": "品牌", "image_url": "图片地址", "platform": "平台名，默认插件 id",
               "in_stock": "是否有货 1/0", "gtin": "条码", "model": "型号", "seller": "卖家", "shipping": "运费"}
class PluginError(Exception): """插件可预期的错误（配置错误、源站返回异常），不会被重试。"""
class RetryableError(Exception): """临时错误（超时、限流），调度器会按指数退避重试。"""
@dataclass
class PluginMeta:
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    sdk: str = SDK_VERSION
    config_schema: dict = field(default_factory=dict)   # {"字段": {"type": "string|number|boolean", "required": bool, "label": "说明", "default": ...}}
    rate_limit_rps: float = 1.0                         # 建议的每秒请求数，调度器按域名限速时参考
class PluginContext:
    """插件运行时上下文：受控的网络访问、日志、事件。插件不应自行创建全局状态。"""
    def __init__(self, plugin_id):
        self.plugin_id = plugin_id; self.log = logging.getLogger(f"pricehub.plugin.{plugin_id}"); self.requests = 0
    def http_get(self, url, headers=None, timeout=20):
        from .. import net
        self.requests += 1
        try: r = net.get(url, headers=headers, timeout=timeout)
        except Exception as e: raise RetryableError(f"网络错误：{e}") from e
        if r.status_code == 429 or r.status_code >= 500: raise RetryableError(f"HTTP {r.status_code}")
        if r.status_code >= 400: raise PluginError(f"HTTP {r.status_code}")
        return r
    def emit(self, topic, **payload):
        from ..core.events import bus
        bus.publish(f"plugin.{self.plugin_id}.{topic}", **payload)
class SourcePlugin(sources.DataSource):
    meta: PluginMeta = None
    @classmethod
    def describe(cls): return asdict(cls.meta)
    def validate_config(self) -> list[str]:
        errs = []
        for k, s in (self.meta.config_schema or {}).items():
            v = self.config.get(k, s.get("default"))
            if s.get("required") and v in (None, ""): errs.append(f"缺少配置项：{s.get('label', k)}")
            elif v is not None and s.get("type") == "number":
                try: float(v)
                except (TypeError, ValueError): errs.append(f"{s.get('label', k)} 必须是数字")
        return errs
    def setup(self, ctx: PluginContext): pass
    def fetch_items(self, ctx: PluginContext) -> list[dict]: raise NotImplementedError
    def teardown(self, ctx: PluginContext): pass
    def healthcheck(self) -> dict: return {"ok": True}
    def fetch(self):
        errs = self.validate_config()
        if errs: raise PluginError("；".join(errs))
        ctx = PluginContext(self.meta.id); t0 = time.time()
        try:
            self.setup(ctx); raw = self.fetch_items(ctx) or []
        finally:
            try: self.teardown(ctx)
            except Exception as e: log.warning("teardown failed", extra={"plugin": self.meta.id, "error": str(e)})
        items = [normalize_item(d, self.meta.id) for d in raw]
        log.info("plugin fetch", extra={"plugin": self.meta.id, "items": len(items), "requests": ctx.requests, "ms": int((time.time() - t0) * 1000)})
        return items
def normalize_item(d: dict, plugin_id: str) -> dict:
    if not isinstance(d, dict): raise PluginError("商品必须是字典")
    if not d.get("title"): raise PluginError("商品缺少 title")
    out = {k: d.get(k) for k in ITEM_FIELDS if d.get(k) is not None}
    out["external_id"] = str(d.get("external_id") or hashlib.md5(str(d.get("url") or d["title"]).encode()).hexdigest()[:12])
    if out.get("price") is not None:
        try: out["price"] = float(str(out["price"]).replace(",", ""))
        except ValueError: raise PluginError(f"价格不是数字：{out['price']}")
    out.setdefault("platform", plugin_id); out.setdefault("currency", "CNY")
    return out
PLUGINS: dict[str, dict] = {}    # id -> {meta, origin, state, error, loaded_at}
def register_plugin(cls, origin="builtin"):
    m = getattr(cls, "meta", None)
    if not isinstance(m, PluginMeta) or not m.id: raise PluginError(f"{cls.__name__} 缺少 meta.id")
    if m.sdk.split(".")[0] != SDK_VERSION.split(".")[0]: raise PluginError(f"插件 SDK 版本 {m.sdk} 与当前 {SDK_VERSION} 不兼容")
    if m.id in sources.REGISTRY and m.id not in PLUGINS:
        raise PluginError(f"插件 id 与内置数据源冲突：{m.id}")
    cls.type = m.id; sources.REGISTRY[m.id] = cls
    PLUGINS[m.id] = {"meta": asdict(m), "origin": origin, "state": "active", "error": None, "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    return cls
def plugin_dir(): return os.environ.get("PRICEHUB_PLUGINS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "plugins"))
def load_dir(path=None):
    """从目录动态加载第三方插件：支持 单文件 xxx.py 与 包目录 xxx/plugin.py。返回 [{file, state, ids, error}]"""
    path = os.path.abspath(path or plugin_dir()); report = []
    if not os.path.isdir(path): return report
    for name in sorted(os.listdir(path)):
        f = os.path.join(path, name)
        if os.path.isdir(f): f = os.path.join(f, "plugin.py")
        if not f.endswith(".py") or not os.path.isfile(f) or os.path.basename(f).startswith("_"): continue
        modname = "pricehub_plugin_" + hashlib.md5(f.encode()).hexdigest()[:8]
        try:
            spec = importlib.util.spec_from_file_location(modname, f); mod = importlib.util.module_from_spec(spec); sys.modules[modname] = mod; spec.loader.exec_module(mod)
            ids = []
            for obj in vars(mod).values():
                if isinstance(obj, type) and issubclass(obj, SourcePlugin) and obj is not SourcePlugin and getattr(obj, "meta", None):
                    register_plugin(obj, origin=f); ids.append(obj.meta.id)
            report.append({"file": f, "state": "active" if ids else "empty", "ids": ids, "error": None if ids else "未找到 SourcePlugin 子类"})
        except Exception as e:
            report.append({"file": f, "state": "error", "ids": [], "error": f"{type(e).__name__}: {e}"}); log.warning("plugin load failed", extra={"file": f, "error": traceback.format_exc(limit=2)})
    return report
LAST_LOAD: list = []
def reload():
    global LAST_LOAD
    for pid, p in list(PLUGINS.items()):
        if p["origin"] != "builtin": PLUGINS.pop(pid); sources.REGISTRY.pop(pid, None)
    LAST_LOAD = load_dir(); return LAST_LOAD
def catalog():
    builtin = [{"id": k, "name": k, "origin": "builtin", "state": "active", "sdk": None} for k in sources.REGISTRY if k not in PLUGINS]
    return {"sdk_version": SDK_VERSION, "dir": os.path.abspath(plugin_dir()), "item_fields": ITEM_FIELDS,
            "plugins": builtin + [dict(id=k, name=v["meta"]["name"], origin=v["origin"], state=v["state"], sdk=v["meta"]["sdk"], meta=v["meta"]) for k, v in PLUGINS.items()], "last_load": LAST_LOAD}
