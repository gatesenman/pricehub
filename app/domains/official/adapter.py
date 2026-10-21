"""官方接口适配器抽象：鉴权、限速、分页、错误映射、审计。
子类实现 auth_headers / fetch_page；框架负责限速、重试映射、统一商品字段、审计。
无真实密钥时也可注册并走 mock_base_url 试跑。
"""
from __future__ import annotations
import time, json, logging, threading, hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional
import httpx
from app.sdk import PluginError, RetryableError
log = logging.getLogger("pricehub.official")
@dataclass
class AdapterMeta:
    id: str
    name: str
    platform: str
    version: str = "1.0"
    description: str = ""
    docs_url: str = ""
    status: str = "ready"          # ready | stub | experimental
    auth_fields: list = field(default_factory=list)
    config_fields: list = field(default_factory=list)
    rate_limit_rps: float = 1.0
    stub_note: str = ""
class RateLimiter:
    def __init__(self): self._next = {}; self._lock = threading.Lock()
    def wait(self, key, rps):
        gap = 1.0 / max(rps, 0.01)
        with self._lock:
            now = time.time(); nxt = self._next.get(key, 0); delay = max(0, nxt - now); self._next[key] = max(now, nxt) + gap
        if delay: time.sleep(delay)
_RL = RateLimiter()
class OfficialApiAdapter:
    meta: AdapterMeta = None
    def __init__(self, credentials: dict = None, config: dict = None, mock_base_url: str = None):
        self.credentials = dict(credentials or {}); self.config = dict(config or {}); self.mock_base_url = (mock_base_url or "").rstrip("/")
        self._audit = []
    def describe(self): return asdict(self.meta)
    def validate(self) -> list[str]:
        errs = []
        for f in self.meta.auth_fields:
            if f.get("required") and not self.credentials.get(f["key"]) and self.meta.status != "stub":
                if not self.mock_base_url: errs.append(f"缺少凭证：{f.get('label', f['key'])}")
        return errs
    def auth_headers(self) -> dict: return {}
    def build_url(self, path: str) -> str:
        if self.mock_base_url: return self.mock_base_url + path
        return (self.config.get("base_url") or "") + path
    def request(self, method, path, **kw):
        _RL.wait(self.meta.id, float(self.config.get("rate_limit_rps") or self.meta.rate_limit_rps))
        url = self.build_url(path); headers = {**(kw.pop("headers", {}) or {}), **self.auth_headers()}
        t0 = time.time()
        try:
            r = httpx.request(method, url, headers=headers, timeout=float(self.config.get("timeout", 20)), **kw)
        except httpx.HTTPError as e:
            self._audit.append({"ok": False, "url": url, "error": str(e), "ms": int((time.time()-t0)*1000)})
            raise RetryableError(f"网络错误：{e}") from e
        entry = {"ok": 200 <= r.status_code < 300, "status": r.status_code, "url": url, "ms": int((time.time()-t0)*1000), "bytes": len(r.content)}
        self._audit.append(entry)
        if r.status_code in (401, 403): raise PluginError(f"鉴权失败 HTTP {r.status_code}")
        if r.status_code == 429 or r.status_code >= 500: raise RetryableError(f"HTTP {r.status_code}")
        if r.status_code >= 400: raise PluginError(self.map_error(r))
        return r
    def map_error(self, r) -> str:
        try: body = r.json()
        except Exception: body = r.text[:200]
        return f"{self.meta.name} 返回 HTTP {r.status_code}：{body}"
    def fetch_page(self, cursor=None) -> tuple[list[dict], Optional[str]]:
        raise NotImplementedError
    def fetch_all(self, max_pages=5) -> list[dict]:
        errs = self.validate()
        if errs: raise PluginError("；".join(errs))
        if self.meta.status == "stub" and not self.mock_base_url:
            raise PluginError(self.meta.stub_note or f"{self.meta.name} 目前是占位适配器，请配置 mock 地址试跑或查阅文档补齐。")
        out, cursor, pages = [], None, 0
        while pages < max_pages:
            items, cursor = self.fetch_page(cursor); out.extend(items or []); pages += 1
            if not cursor: break
        return out
    def audit_log(self): return list(self._audit)
    def test_connection(self) -> dict:
        try:
            items, _ = self.fetch_page(None)
            return {"ok": True, "sample": (items or [])[:2], "count": len(items or []), "audit": self.audit_log()}
        except Exception as e:
            return {"ok": False, "error": str(e), "audit": self.audit_log()}
class _JsonListAdapter(OfficialApiAdapter):
    path = "/search"
    def auth_headers(self):
        tok = self.credentials.get("api_key") or self.credentials.get("access_token") or self.credentials.get("access_key") or ""
        return {"Authorization": f"Bearer {tok}"} if tok else {}
    def fetch_page(self, cursor=None):
        params = {"q": self.config.get("query", "headphones"), "page": cursor or "1", "limit": int(self.config.get("page_size", 10))}
        r = self.request("GET", self.path, params=params)
        data = r.json()
        if isinstance(data, list): items, nxt = data, None
        else: items, nxt = data.get("items") or data.get("results") or [], data.get("next")
        norm = []
        for it in items:
            norm.append({"external_id": str(it.get("id") or it.get("asin") or it.get("itemId") or hashlib.md5(json.dumps(it, sort_keys=True, default=str).encode()).hexdigest()[:12]),
                         "title": it.get("title") or it.get("name"), "price": it.get("price"), "currency": it.get("currency") or "USD",
                         "url": it.get("url") or it.get("link"), "platform": self.meta.platform, "image_url": it.get("image"), "seller": it.get("seller")})
        return norm, nxt
def _meta(id, name, platform, docs="", status="ready", fields=None, note="", rps=1.0, desc=""):
    return AdapterMeta(id=id, name=name, platform=platform, docs_url=docs, status=status, stub_note=note, rate_limit_rps=rps, description=desc,
                       auth_fields=fields or [{"key": "api_key", "label": "API Key", "secret": True, "required": True}])
class AmazonPAAPI(_JsonListAdapter):
    meta = _meta("amazon_paapi5", "Amazon Product Advertising API 5", "amazon.com", "https://webservices.amazon.com/paapi5/documentation/",
                 fields=[{"key": "access_key", "label": "Access Key", "secret": True, "required": True},
                         {"key": "secret_key", "label": "Secret Key", "secret": True, "required": True},
                         {"key": "partner_tag", "label": "Partner Tag", "secret": False, "required": True}], rps=1,
                 desc="亚马逊联盟商品搜索。真实调用需 AWS SigV4；当前 mock 模式用 Bearer 简化。")
    path = "/paapi5/search"
    def auth_headers(self):
        return {"Authorization": f"Bearer {self.credentials.get('access_key','')}", "X-Partner-Tag": self.credentials.get("partner_tag", "")}
class EbayBrowse(_JsonListAdapter):
    meta = _meta("ebay_browse", "eBay Browse API", "ebay.com", "https://developer.ebay.com/api-docs/buy/browse/overview.html",
                 fields=[{"key": "access_token", "label": "OAuth Access Token", "secret": True, "required": True}], rps=5)
    path = "/buy/browse/v1/item_summary/search"
class AliExpressAffiliate(_JsonListAdapter):
    meta = _meta("aliexpress_affiliate", "AliExpress Affiliate", "aliexpress.com", "https://openservice.aliexpress.com/", rps=2)
    path = "/affiliate/product/query"
class JDUnion(_JsonListAdapter):
    meta = _meta("jd_union", "京东联盟", "jd.com", "https://union.jd.com/",
                 fields=[{"key": "app_key", "label": "App Key", "secret": False, "required": True},
                         {"key": "app_secret", "label": "App Secret", "secret": True, "required": True}], rps=2)
    path = "/jdunion/goods"
class TaobaoOpen(_JsonListAdapter):
    meta = _meta("taobao_open", "淘宝开放平台 / 开放搜索", "taobao.com", "https://open.taobao.com/",
                 status="stub", note="公开文档对零售价格拉取限制较多，当前为占位适配器：配置 mock_base_url 可试跑；正式接入需企业资质与服务市场订购。",
                 fields=[{"key": "app_key", "label": "App Key", "secret": False, "required": True},
                         {"key": "app_secret", "label": "App Secret", "secret": True, "required": True}])
    path = "/taobao/items"
class KeepaAdapter(_JsonListAdapter):
    meta = _meta("keepa", "Keepa Product API", "amazon.com", "https://keepa.com/#!discuss/t/api", rps=0.5, desc="亚马逊历史价格第三方")
    path = "/keepa/product"
class SerpApiGoogleShopping(_JsonListAdapter):
    meta = _meta("serpapi_google_shopping", "SerpAPI Google Shopping", "google.com", "https://serpapi.com/google-shopping-api", rps=1)
    path = "/search.json"
REGISTRY = {c.meta.id: c for c in (AmazonPAAPI, EbayBrowse, AliExpressAffiliate, JDUnion, TaobaoOpen, KeepaAdapter, SerpApiGoogleShopping)}
def list_adapters(): return [asdict(c.meta) for c in REGISTRY.values()]
def make(adapter_id, credentials=None, config=None, mock_base_url=None):
    cls = REGISTRY.get(adapter_id)
    if not cls: raise KeyError(f"未知官方适配器：{adapter_id}")
    return cls(credentials=credentials, config=config, mock_base_url=mock_base_url)
