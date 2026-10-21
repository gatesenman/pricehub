"""示例插件：读取任意 JSON 商品源（如商家开放的商品 feed、内部 ERP 导出接口）。
配置：url（必填）、items_key（列表所在字段，默认 items）、字段映射 id_field/title_field/price_field。"""
from app.sdk import SourcePlugin, PluginMeta, PluginError
class JsonFeedPlugin(SourcePlugin):
    meta = PluginMeta(
        id="jsonfeed", name="JSON 商品源", version="1.0.0", author="PriceHub", description="读取返回 JSON 的商品列表接口",
        config_schema={"url": {"type": "string", "required": True, "label": "接口地址"},
                       "items_key": {"type": "string", "default": "items", "label": "列表字段"},
                       "id_field": {"type": "string", "default": "id", "label": "编号字段"},
                       "title_field": {"type": "string", "default": "name", "label": "标题字段"},
                       "price_field": {"type": "string", "default": "price", "label": "价格字段"}},
        rate_limit_rps=2)
    def fetch_items(self, ctx):
        c = self.config; data = ctx.http_get(c["url"]).json()
        rows = data.get(c.get("items_key") or "items") if isinstance(data, dict) else data
        if not isinstance(rows, list): raise PluginError("接口返回里找不到商品列表")
        g = lambda r, k, d: r.get(c.get(k) or d)
        return [{"external_id": g(r, "id_field", "id"), "title": g(r, "title_field", "name"), "price": g(r, "price_field", "price"),
                 "currency": r.get("currency", "CNY"), "url": r.get("url"), "brand": r.get("brand"), "category": r.get("category"),
                 "in_stock": r.get("in_stock"), "gtin": r.get("gtin"), "platform": c.get("platform") or "jsonfeed"} for r in rows]
