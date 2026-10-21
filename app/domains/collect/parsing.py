"""通用商品页/搜索页解析 + 页面状态判定（供平台数据源与连通性体检共用）。"""
import json, re
from bs4 import BeautifulSoup
PRICE_RE = re.compile(r"(?:¥|￥|\$|€|£|₩|₹|R\$|RM|Rp|USD|EUR|CNY|JPY)\s?(\d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
def num(t):
    if t is None: return None
    t = str(t).strip(); m = re.search(r"\d[\d.,\s]*", t)
    if not m: return None
    v = m.group(0).replace(" ", "")
    if re.search(r",\d{2}$", v) and "." in v: v = v.replace(".", "").replace(",", ".")
    elif re.search(r",\d{2}$", v): v = v.replace(",", ".")
    else: v = v.replace(",", "")
    try: return float(v)
    except ValueError: return None
def _jsonld(soup):
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try: d = json.loads(s.string or "")
        except Exception: continue
        stack = d if isinstance(d, list) else [d]
        while stack:
            x = stack.pop()
            if isinstance(x, list): stack += x; continue
            if not isinstance(x, dict): continue
            t = x.get("@type"); t = t if isinstance(t, list) else [t]
            if "Product" in t: out.append(x)
            for k in ("@graph", "itemListElement", "item"): 
                if k in x: stack.append(x[k])
    return out
def _offer(p):
    o = p.get("offers") or {}
    o = o[0] if isinstance(o, list) and o else o
    price = o.get("price") or o.get("lowPrice") if isinstance(o, dict) else None
    return num(price), (o.get("priceCurrency") if isinstance(o, dict) else None)
def _avail(v):
    """schema.org availability → True(有货)/False(缺货/下架)/None(未知)。"""
    if not v: return None
    v = str(v).lower()
    if any(k in v for k in ("outofstock", "soldout", "discontinued", "out of stock")): return False
    if any(k in v for k in ("instock", "limitedavailability", "preorder", "onlineonly", "in stock")): return True
    return None
def _extra(p):
    """从 JSON-LD Product 读取 GTIN/品牌/型号/库存/运费/多规格。"""
    offers = p.get("offers") or {}
    offs = offers if isinstance(offers, list) else (offers.get("offers") if isinstance(offers.get("offers"), list) else [offers])
    b = p.get("brand"); b = b.get("name") if isinstance(b, dict) else b
    gtin = next((str(p[k]) for k in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8", "ean") if p.get(k)), None)
    o0 = offs[0] if offs and isinstance(offs[0], dict) else {}
    ship = None
    sd = o0.get("shippingDetails")
    sd = sd[0] if isinstance(sd, list) and sd else sd
    if isinstance(sd, dict): ship = num((sd.get("shippingRate") or {}).get("value"))
    variants = [{"name": o.get("name") or o.get("sku") or f"规格{i+1}", "sku": o.get("sku"), "price": num(o.get("price")), "in_stock": _avail(o.get("availability"))}
                for i, o in enumerate(offs) if isinstance(o, dict) and num(o.get("price"))] if len(offs) > 1 else []
    return {k: v for k, v in dict(gtin=gtin, brand=b, model=p.get("mpn") or p.get("model") if isinstance(p.get("model"), (str, type(None))) else None, sku=p.get("sku"),
            in_stock=_avail(o0.get("availability")), shipping=ship, variants=variants or None).items() if v is not None}
def parse_product(html, url="", rule=None):
    rule = rule or {}; s = BeautifulSoup(html, "html.parser")
    title = None; img = (s.find("meta", property="og:image") or {}).get("content")
    for p in _jsonld(s):
        price, cur = _offer(p)
        if price: return dict(title=p.get("name"), price=price, currency=cur, method="jsonld", image_url=img or (p.get("image") if isinstance(p.get("image"), str) else None), url=url, **_extra(p))
    if rule.get("page_price_sel") and s.select_one(rule["page_price_sel"]):
        t = s.select_one(rule.get("page_title_sel") or "h1")
        return dict(title=t.get_text(strip=True) if t else None, price=num(s.select_one(rule["page_price_sel"]).get_text()), currency=None, method="css", image_url=img, url=url)
    ip = s.find(attrs={"itemprop": "price"})
    title = (s.find("meta", property="og:title") or {}).get("content") or (s.title.string.strip() if s.title and s.title.string else None)
    if ip:
        cur = s.find(attrs={"itemprop": "priceCurrency"}); av = s.find(attrs={"itemprop": "availability"})
        d = dict(title=title, price=num(ip.get("content") or ip.get_text()), currency=cur and (cur.get("content") or cur.get_text()), method="microdata", image_url=img, url=url)
        if av is not None and _avail(av.get("href") or av.get("content") or av.get_text()) is not None: d["in_stock"] = _avail(av.get("href") or av.get("content") or av.get_text())
        return d
    for prop in ("product:price:amount", "og:price:amount"):
        m = s.find("meta", property=prop)
        if m and num(m.get("content")):
            c = s.find("meta", property=prop.replace("amount", "currency"))
            return dict(title=title, price=num(m.get("content")), currency=c and c.get("content"), method="meta", image_url=img, url=url)
    m = PRICE_RE.search(s.get_text(" "))
    if m: return dict(title=title, price=num(m.group(1)), currency=None, method="regex", image_url=img, url=url)
    return None
def parse_search(html, base="", rule=None, limit=20):
    rule = rule or {}; s = BeautifulSoup(html, "html.parser"); out = []
    for p in _jsonld(s):
        price, cur = _offer(p)
        if price: out.append(dict(title=p.get("name"), price=price, currency=cur, method="jsonld", url=p.get("url") or base))
    if not out and rule.get("item_sel"):
        for it in s.select(rule["item_sel"])[:limit]:
            t, pr = it.select_one(rule.get("title_sel") or "a"), it.select_one(rule.get("price_sel") or ".price")
            a = it.find("a", href=True)
            if t and pr and num(pr.get_text()): out.append(dict(title=t.get_text(strip=True), price=num(pr.get_text()), currency=None, method="css", url=a["href"] if a else base))
    return out[:limit]
ANTIBOT = ("captcha", "cf-chl", "challenge-platform", "attention required", "access denied", "are you a robot", "robot check", "verify you are human", "perimeterx", "px-captcha", "datadome", "滑块", "安全验证")
def classify(status, html, final_url=""):
    """返回 (状态, 说明)。状态：可达/解析成功/需JS渲染/反爬拦截/域名失效/不可达。"""
    low = (html or "")[:200000].lower()
    if status in (401, 403, 429, 503) and any(k in low for k in ANTIBOT + ("cloudflare",)): return "反爬拦截", f"HTTP {status} 验证/拦截页"
    if any(k in low for k in ANTIBOT): return "反爬拦截", "页面含验证码/机器人校验"
    if status in (403, 429): return "反爬拦截", f"HTTP {status}"
    if status >= 500: return "不可达", f"HTTP {status}"
    if status == 404: return "可达", "HTTP 404（首页/搜索路径不存在）"
    s = BeautifulSoup(html or "", "html.parser")
    if _jsonld(s) or s.find(attrs={"itemprop": "price"}) or s.find("meta", property="product:price:amount"): return "解析成功", "发现 JSON-LD/微数据/meta 价格"
    text = s.get_text(" ", strip=True); scripts = len(s.find_all("script"))
    if len(text) < 400 and scripts >= 3 or "enable javascript" in low or "<noscript>you need to enable" in low or 'id="__next"' in low and len(text) < 1500:
        return "需JS渲染", f"正文 {len(text)} 字，脚本 {scripts} 个"
    if PRICE_RE.search(text): return "解析成功", "页面文本中识别到价格"
    return "可达", f"HTTP {status}，未发现结构化价格"
