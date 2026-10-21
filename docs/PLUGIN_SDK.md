# 数据源插件 SDK 开发指南（SDK 1.0）

## 快速开始
1. 新建 `plugins/myshop/plugin.py`（或单文件 `plugins/myshop.py`）。
2. 写一个继承 `SourcePlugin` 的类：

```python
from app.sdk import SourcePlugin, PluginMeta, PluginError

class MyShop(SourcePlugin):
    meta = PluginMeta(
        id="myshop", name="我的商城", version="1.0.0", author="你",
        config_schema={"url": {"type": "string", "required": True, "label": "接口地址"},
                       "pages": {"type": "number", "default": 1, "label": "页数"}},
        rate_limit_rps=1)

    def fetch_items(self, ctx):
        data = ctx.http_get(self.config["url"]).json()
        return [{"external_id": r["sku"], "title": r["name"], "price": r["price"], "currency": "CNY",
                 "url": r.get("link"), "in_stock": r.get("stock", 0) > 0} for r in data["list"]]
```

3. 重启服务，或在「设置与集成 > 数据源插件」点「重新加载」，或调用 `POST /api/v1/plugins/reload`。
4. 试跑：`POST /api/v1/plugins/myshop/test`，请求体 `{"config": {"url": "..."}}`，返回条数和前 5 条样例。
5. 新建数据源时类型填 `myshop`，之后由调度器按域名限速、失败重试、进死信。

## 接口约定
| 方法 | 是否必须 | 说明 |
|---|---|---|
| `meta` | 必须 | `PluginMeta(id, name, version, description, author, homepage, sdk, config_schema, rate_limit_rps)` |
| `fetch_items(ctx)` | 必须 | 返回商品字典列表 |
| `validate_config()` | 可选 | 默认按 `config_schema` 检查必填和数字类型，返回错误列表 |
| `setup(ctx)` / `teardown(ctx)` | 可选 | 采集前后调用，`teardown` 在异常时也会调用 |
| `healthcheck()` | 可选 | 返回 `{"ok": bool, ...}` |

商品字段：`external_id`、`title`、`price` 必填；可选 `currency, url, category, brand, image_url, platform, in_stock, gtin, model, seller, shipping`。

## 错误处理
- `RetryableError`：临时问题（超时、429、5xx），调度器按指数退避重试。`ctx.http_get` 已自动转换。
- `PluginError`：配置错误、源站 4xx、数据格式不对，不重试，直接记入死信并在界面显示原因。

## 规则
- 插件 id 不能和内置类型（mock、webpage、rest、csv、platform）重名。
- `meta.sdk` 主版本必须与平台一致（当前 1）。
- 只访问你有权访问的数据；遵守目标站点的 robots.txt 和服务条款；不要在插件里绕过验证码或登录保护。
- 插件和主程序同权限运行，只安装可信来源的插件。
