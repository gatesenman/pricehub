"""API 版本化：/api/v1/* 为正式版本路径，内部重写到现有 /api/* 路由；旧路径继续可用（响应头标注 Deprecation 提示）。
新增接口直接写在 /api/... 即可同时获得两条路径；v2 出现不兼容变更时再按前缀分路由。"""
CURRENT = "v1"
class ApiVersionMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        path = scope["path"]; versioned = path.startswith("/api/v1/") or path == "/api/v1"
        if versioned:
            scope = dict(scope); scope["path"] = "/api" + path[len("/api/v1"):]; scope["raw_path"] = scope["path"].encode()
            scope["pricehub_api_version"] = "v1"
        legacy = path.startswith("/api/") and not versioned
        async def _send(m):
            if m["type"] == "http.response.start":
                h = list(m.get("headers", [])); h.append((b"x-api-version", CURRENT.encode()))
                if legacy: h.append((b"x-api-legacy-path", b"true; use /api/v1"))
                m = dict(m, headers=h)
            await send(m)
        return await self.app(scope, receive, _send)
