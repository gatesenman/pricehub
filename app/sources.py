"""插件化数据源：继承 DataSource 并用 @register 注册即可扩展任意平台。"""
import json, re, random, hashlib, httpx
from bs4 import BeautifulSoup
REGISTRY = {}
def register(cls): REGISTRY[cls.type] = cls; return cls

class DataSource:
    type = "base"
    def __init__(self, config: dict): self.config = config or {}
    def fetch(self) -> list[dict]:
        """返回统一商品结构 [{external_id,title,price,category,chain,brand,url,image_url,platform}]"""
        raise NotImplementedError

@register
class MockSource(DataSource):
    type = "mock"
    CATALOG = [("iPhone 16 Pro 256GB", "手机", "消费电子>手机", "Apple", 7999),
               ("小米14 Ultra 16+512", "手机", "消费电子>手机", "Xiaomi", 5999),
               ("Steam 充值卡 100元", "虚拟商品", "数字内容>游戏点卡", "Steam", 100),
               ("Switch OLED 游戏机", "游戏机", "消费电子>游戏", "Nintendo", 2099),
               ("A4 复印纸 70g 5包", "办公耗材", "工业品>办公>纸张", "得力", 129)]
    def fetch(self):
        plat = self.config.get("platform", "mock"); bias = self.config.get("bias", 1.0); seed = self.config.get("seed")
        rnd = random.Random(seed) if seed is not None else random
        sellers = self.config.get("sellers") or [f"{plat} 自营", f"{plat} 官方旗舰店", f"{plat} 第三方商家"]
        return [dict(external_id=hashlib.md5(t.encode()).hexdigest()[:8], title=t, category=c, chain=ch, brand=b, method="mock",
                     price=round(p * bias * rnd.uniform(0.95, 1.05), 2), platform=plat, url="", image_url=None,
                     seller=sellers[i % len(sellers)], attrs={"品牌": b, "发货地": self.config.get("ship_from", "中国"), "保修": "1年" if c != "虚拟商品" else "无"})
                for i, (t, c, ch, b, p) in enumerate(self.CATALOG)]

@register
class WebPageSource(DataSource):
    """通用网页适配器：config={urls:[...], title_selector, price_selector, platform}；无选择器时用 og/meta/正则回退。"""
    type = "webpage"
    def fetch(self):
        out = []
        for url in self.config.get("urls", []):
            html = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "PriceHub/0.1"}).text
            s = BeautifulSoup(html, "html.parser")
            ts, ps = self.config.get("title_selector"), self.config.get("price_selector")
            title = (s.select_one(ts).get_text(strip=True) if ts and s.select_one(ts) else
                     (s.find("meta", property="og:title") or {}).get("content") or (s.title.string if s.title else url))
            if ps and s.select_one(ps): ptxt, method = s.select_one(ps).get_text(), "css"
            elif s.find("meta", property="product:price:amount"): ptxt, method = s.find("meta", property="product:price:amount").get("content"), "meta"
            else: ptxt, method = s.get_text(), "regex"
            m = re.search(r"(\d+(?:[.,]\d{1,2})?)", ptxt.replace(",", "")) if ptxt else None
            img = (s.find("meta", property="og:image") or {}).get("content")
            out.append(dict(external_id=hashlib.md5(url.encode()).hexdigest()[:10], title=title.strip(), price=float(m.group(1)) if m else None,
                            category=self.config.get("category", ""), chain=self.config.get("chain", ""), brand="",
                            url=url, image_url=img, platform=self.config.get("platform", "web"), method=method))
        return out

def _get(d, path):
    for k in path.split("."):
        d = d[int(k)] if isinstance(d, list) else d.get(k) if d is not None else None
    return d

@register
class RestSource(DataSource):
    """第三方私有 API：config={url, method, headers:{..}, params, items_path:'data.items',
       mapping:{title:'name', price:'pricing.amount', external_id:'sku', ...}, platform}
       headers 值支持 ${ENV} 引用环境变量。"""
    type = "rest"
    def fetch(self):
        import os
        h = {k: re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v) for k, v in self.config.get("headers", {}).items()}
        r = httpx.request(self.config.get("method", "GET"), self.config["url"], headers=h, params=self.config.get("params"), timeout=15)
        r.raise_for_status()
        items = _get(r.json(), self.config["items_path"]) if self.config.get("items_path") else r.json()
        mp = self.config.get("mapping", {})
        out = []
        for it in items:
            rec = {k: _get(it, v) for k, v in mp.items()}
            rec.setdefault("platform", self.config.get("platform", "rest")); rec["method"] = "api"
            rec["external_id"] = str(rec.get("external_id") or hashlib.md5(str(rec.get("title")).encode()).hexdigest()[:10])
            if rec.get("price") is not None: rec["price"] = float(rec["price"])
            out.append(rec)
        return out

def make(type_, config): return REGISTRY[type_](config)

@register
class CsvSource(DataSource):
    """CSV 导入：config={path 或 url, mapping:{title:'商品名', price:'价格', ...}, platform}；无 mapping 时按同名列。"""
    type = "csv"
    def fetch(self):
        import csv, io
        txt = httpx.get(self.config["url"], timeout=15).text if self.config.get("url") else open(self.config["path"], encoding="utf-8-sig").read()
        mp = self.config.get("mapping") or {}
        out = []
        for row in csv.DictReader(io.StringIO(txt)):
            rec = {k: row.get(v) for k, v in mp.items()} if mp else dict(row)
            STD = {"external_id","title","price","cost","category","chain","brand","platform","group_key","url","seller","currency","image_url"}
            rec = {k: v for k, v in rec.items() if k in STD} | {"attrs": {k: v for k, v in rec.items() if k not in STD and v}}
            rec.setdefault("platform", self.config.get("platform", "csv")); rec["method"] = "structured"
            rec["external_id"] = str(rec.get("external_id") or hashlib.md5(str(rec.get("title")).encode()).hexdigest()[:10])
            if rec.get("price") not in (None, ""): rec["price"] = float(rec["price"])
            if rec.get("cost") not in (None, ""): rec["cost"] = float(rec["cost"])
            out.append(rec)
        return out


@register
class PlatformSource(DataSource):
    """内置平台（920 个之一）：config={platform_slug, urls:[商品页], query:'关键词'}；按平台专属规则或通用策略
       （JSON-LD/schema.org → 微数据 → meta → 正则）解析，提取方式决定价格置信度。"""
    type = "platform"
    def fetch(self):
        from . import db, parsing
        slug = self.config.get("platform_slug") or ""
        pf = db.q("SELECT * FROM platforms WHERE slug=?", (slug,), one=True) if slug else None
        if not pf and slug: raise ValueError("未知平台")
        if not pf:   # 未收录的网站：按通用策略解析
            dom = self.config.get("platform") or "网页"
            pf = {"rules": "{}", "lang": "zh-CN,en", "domain": dom, "name": dom, "currency": self.config.get("currency") or "CNY", "search_url": ""}
        rule = json.loads(pf["rules"] or "{}"); H = {"User-Agent": "Mozilla/5.0 PriceHub/0.3", "Accept-Language": pf["lang"] or "en"}
        out = []
        def item(d):
            d.update(platform=pf["domain"] or pf["name"], currency=d.get("currency") or pf["currency"], category=self.config.get("category", ""),
                     external_id=hashlib.md5((d.get("url") or d.get("title") or "").encode()).hexdigest()[:10], seller=pf["name"])
            out.append(d)
        from . import collect
        for url in self.config.get("urls", []):
            d, st, why, code = collect.fetch_parse(url, rule, pf["lang"] or "en")
            if not d or not d.get("price"):
                if st == "反爬拦截": raise ValueError(f"{url}: 反爬拦截（{why}）")
                raise ValueError(f"{url}: 未解析到价格（{st}：{why}）")
            item(d)
        if self.config.get("query"):
            su = (self.config.get("search_url") or pf["search_url"]).replace("{q}", httpx.QueryParams({"q": self.config["query"]})["q"].replace(" ", "+"))
            r = httpx.get(su, timeout=15, follow_redirects=True, headers=H)
            res = parsing.parse_search(r.text, su, rule)
            if not res:
                st, why = parsing.classify(r.status_code, r.text); raise ValueError(f"搜索页无结果（{st}：{why}）")
            for d in res: item(d)
        return out
