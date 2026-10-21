"""把《全球电商平台名录》920 条全部内置为平台数据源定义：域名、语言/币种、搜索 URL 模板、解析策略。
主流平台配专属规则（DEDICATED），其余走通用策略：站内搜索探测 + JSON-LD/schema.org Product + og/meta 价格 + 正则回退。"""
import csv, os, re, json, hashlib
HERE = os.path.dirname(__file__)
# 国家 -> (语言, 币种)；未列出的按大洲回退
COUNTRY = {"中国": ("zh-CN", "CNY"), "香港": ("zh-HK", "HKD"), "澳门": ("zh-HK", "MOP"), "台湾": ("zh-TW", "TWD"), "日本": ("ja", "JPY"), "韩国": ("ko", "KRW"),
  "朝鲜": ("ko", "KPW"), "蒙古": ("mn", "MNT"), "新加坡": ("en", "SGD"), "马来西亚": ("ms", "MYR"), "印度尼西亚": ("id", "IDR"), "泰国": ("th", "THB"), "越南": ("vi", "VND"),
  "菲律宾": ("en", "PHP"), "印度": ("en", "INR"), "巴基斯坦": ("ur", "PKR"), "孟加拉国": ("bn", "BDT"), "斯里兰卡": ("en", "LKR"), "尼泊尔": ("ne", "NPR"),
  "美国": ("en", "USD"), "加拿大": ("en", "CAD"), "墨西哥": ("es", "MXN"), "巴西": ("pt", "BRL"), "阿根廷": ("es", "ARS"), "智利": ("es", "CLP"), "哥伦比亚": ("es", "COP"), "秘鲁": ("es", "PEN"),
  "英国": ("en", "GBP"), "德国": ("de", "EUR"), "法国": ("fr", "EUR"), "意大利": ("it", "EUR"), "西班牙": ("es", "EUR"), "荷兰": ("nl", "EUR"), "比利时": ("nl", "EUR"), "奥地利": ("de", "EUR"),
  "爱尔兰": ("en", "EUR"), "葡萄牙": ("pt", "EUR"), "希腊": ("el", "EUR"), "芬兰": ("fi", "EUR"), "瑞典": ("sv", "SEK"), "挪威": ("no", "NOK"), "丹麦": ("da", "DKK"), "瑞士": ("de", "CHF"),
  "波兰": ("pl", "PLN"), "捷克": ("cs", "CZK"), "匈牙利": ("hu", "HUF"), "罗马尼亚": ("ro", "RON"), "保加利亚": ("bg", "BGN"), "俄罗斯": ("ru", "RUB"), "乌克兰": ("uk", "UAH"),
  "土耳其": ("tr", "TRY"), "沙特阿拉伯": ("ar", "SAR"), "阿联酋": ("ar", "AED"), "以色列": ("he", "ILS"), "埃及": ("ar", "EGP"), "尼日利亚": ("en", "NGN"), "肯尼亚": ("en", "KES"),
  "南非": ("en", "ZAR"), "摩洛哥": ("fr", "MAD"), "澳大利亚": ("en", "AUD"), "新西兰": ("en", "NZD"), "哈萨克斯坦": ("ru", "KZT"), "乌兹别克斯坦": ("uz", "UZS")}
REGION = {"亚洲": ("en", "USD"), "欧洲": ("en", "EUR"), "非洲": ("en", "USD"), "美洲": ("es", "USD"), "大洋洲": ("en", "AUD"), "南极洲": ("en", "USD")}
# 专属规则：域名正则 -> 搜索模板 + 解析规则（css 选择器 / 需要 JS / 备注）
DEDICATED = [
 (r"(^|\.)amazon\.", {"key": "amazon", "search": "https://{domain}/s?k={q}", "item_sel": "div[data-component-type=s-search-result]", "title_sel": "h2 span", "price_sel": ".a-price .a-offscreen", "page_price_sel": "#corePrice_feature_div .a-offscreen, .a-price .a-offscreen", "page_title_sel": "#productTitle"}),
 (r"(^|\.)ebay\.", {"key": "ebay", "search": "https://{domain}/sch/i.html?_nkw={q}", "item_sel": "li.s-item, li.s-card", "title_sel": ".s-item__title, .s-card__title", "price_sel": ".s-item__price, .s-card__price", "page_price_sel": ".x-price-primary span", "page_title_sel": "h1.x-item-title__mainTitle"}),
 (r"aliexpress\.", {"key": "aliexpress", "search": "https://{domain}/w/wholesale-{q}.html", "js": True}),
 (r"(^|\.)taobao\.com$", {"key": "taobao", "search": "https://s.taobao.com/search?q={q}", "js": True, "login": True}),
 (r"tmall\.com", {"key": "tmall", "search": "https://list.tmall.com/search_product.htm?q={q}", "js": True, "login": True}),
 (r"(^|\.)jd\.com$", {"key": "jd", "search": "https://search.jd.com/Search?keyword={q}", "js": True, "login": True}),
 (r"pinduoduo|yangkeduo|temu\.com", {"key": "pdd", "search": "https://www.temu.com/search_result.html?search_key={q}", "js": True}),
 (r"rakuten\.co\.jp", {"key": "rakuten", "search": "https://search.rakuten.co.jp/search/mall/{q}/", "item_sel": ".searchresultitem", "title_sel": ".title-link--3Ho6z, h2 a", "price_sel": ".price--3zUvK, .important", "page_price_sel": ".price2, .value"}),
 (r"mercari", {"key": "mercari", "search": "https://jp.mercari.com/search?keyword={q}", "js": True}),
 (r"shopee\.", {"key": "shopee", "search": "https://{domain}/search?keyword={q}", "js": True}),
 (r"lazada\.", {"key": "lazada", "search": "https://{domain}/catalog/?q={q}", "js": True}),
 (r"mercadoli[bv]re\.", {"key": "mercadolibre", "search": "https://listado.{domain}/{q}", "item_sel": "li.ui-search-layout__item", "title_sel": "h3, h2", "price_sel": ".andes-money-amount__fraction", "page_price_sel": ".andes-money-amount__fraction"}),
 (r"walmart\.", {"key": "walmart", "search": "https://{domain}/search?q={q}", "js": True, "antibot": True}),
 (r"coupang\.com", {"key": "coupang", "search": "https://{domain}/np/search?q={q}", "item_sel": "li.search-product", "title_sel": ".name", "price_sel": ".price-value", "antibot": True}),
 (r"flipkart\.com", {"key": "flipkart", "search": "https://www.flipkart.com/search?q={q}", "price_sel": "div.Nx9bqj, div._30jeq3"}),
 (r"allegro\.", {"key": "allegro", "search": "https://{domain}/listing?string={q}", "antibot": True}),
 (r"ozon\.", {"key": "ozon", "search": "https://{domain}/search/?text={q}", "js": True, "antibot": True}),
 (r"wildberries\.", {"key": "wildberries", "search": "https://{domain}/catalog/0/search.aspx?search={q}", "js": True}),
 (r"(^|\.)noon\.com", {"key": "noon", "search": "https://www.noon.com/uae-en/search/?q={q}", "js": True}),
 (r"jumia\.", {"key": "jumia", "search": "https://{domain}/catalog/?q={q}", "item_sel": "article.prd", "title_sel": ".name", "price_sel": ".prc"}),
 (r"etsy\.com", {"key": "etsy", "search": "https://www.etsy.com/search?q={q}", "antibot": True}),
 (r"bol\.com", {"key": "bol", "search": "https://www.bol.com/nl/nl/s/?searchtext={q}"}),
 (r"zalando\.", {"key": "zalando", "search": "https://{domain}/catalog/?q={q}"}),
 (r"emag\.", {"key": "emag", "search": "https://{domain}/search/{q}", "item_sel": ".card-item", "title_sel": ".card-v2-title", "price_sel": ".product-new-price"}),
 (r"trendyol\.com|hepsiburada\.com", {"key": "tr", "search": "https://{domain}/sr?q={q}", "js": True}),
 (r"tokopedia\.com", {"key": "tokopedia", "search": "https://www.tokopedia.com/search?q={q}", "js": True}),
 (r"bestbuy\.com", {"key": "bestbuy", "search": "https://www.bestbuy.com/site/searchpage.jsp?st={q}", "antibot": True}),
 (r"target\.com", {"key": "target", "search": "https://www.target.com/s?searchTerm={q}", "js": True}),
 (r"kaspi\.kz", {"key": "kaspi", "search": "https://kaspi.kz/shop/search/?text={q}", "js": True}),
 (r"daraz\.", {"key": "daraz", "search": "https://{domain}/catalog/?q={q}", "js": True}),
]
GENERIC_SEARCH = ["https://{domain}/search?q={q}", "https://{domain}/search?keyword={q}", "https://{domain}/catalogsearch/result/?q={q}", "https://{domain}/?s={q}&post_type=product"]
CLOSED_KW = ("停止运营", "已关闭", "关闭", "停止实物", "已退出", "退出", "关停")
def slug(row): return hashlib.md5((row["国家"] + "|" + row["平台名称"]).encode()).hexdigest()[:10]
def domain_of(url):
    u = (url or "").strip()
    if not u or u in ("—", "-") or "." not in u: return ""
    return re.sub(r"^https?://", "", u).split("/")[0].lower()
def build(row):
    d = domain_of(row.get("网址")); region = row["大洲/地区"].split("-")[0]
    lang, cur = COUNTRY.get(row["国家"], REGION.get(region, ("en", "USD")))
    rule = next((dict(r) for pat, r in DEDICATED if d and re.search(pat, d)), None)
    note = row.get("简要说明") or ""
    status = "无网址" if not d else ("已关停" if any(k in note for k in CLOSED_KW) and "待核实" not in note[:3] else "未检测")
    return {"slug": slug(row), "name": row["平台名称"], "country": row["国家"], "region": row["大洲/地区"], "type": row.get("类型") or "", "scope": row.get("本土或国际") or "",
            "domain": d, "url": row.get("网址") or "", "lang": lang, "currency": cur, "note": note,
            "strategy": ("专属:" + rule["key"]) if rule else ("通用" if d else "无"),
            "search_url": (rule["search"] if rule else GENERIC_SEARCH[0]).replace("{domain}", d) if d else "",
            "rules": json.dumps(rule or {"generic": True, "search_candidates": GENERIC_SEARCH}, ensure_ascii=False), "status": status}
def load_rows(path=None):
    path = path or os.path.join(HERE, "..", "data", "platforms.csv")
    return [build(r) for r in csv.DictReader(open(path, encoding="utf-8-sig"))]
