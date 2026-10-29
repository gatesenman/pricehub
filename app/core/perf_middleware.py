"""v14：静态资源长期缓存 + gzip，减轻首屏重复下载与传输体积。"""
from __future__ import annotations
import os
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# vendor / 图标几乎不变；业务 js/css 用较短缓存，升级安装包后仍会很快刷新
LONG = ("/static/vendor/", "/static/icons/")
SHORT = ("/static/",)


class StaticCacheMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                # 不覆盖已有 Cache-Control
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    if any(path.startswith(p) for p in LONG):
                        headers.append((b"cache-control", b"public, max-age=604800, immutable"))
                    elif any(path.startswith(p) for p in SHORT):
                        headers.append((b"cache-control", b"public, max-age=300"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrap)


def install(app):
    """挂到 FastAPI：先 gzip，再 cache（外层先执行）。"""
    if os.environ.get("PRICEHUB_NO_GZIP") == "1":
        app.add_middleware(StaticCacheMiddleware)
        return
    app.add_middleware(StaticCacheMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=500)
