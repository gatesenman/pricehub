"""v12 同款去重：标题归一化与属性抽取（品牌 / 型号 / 容量 / 颜色 / 条码）。纯函数，便于单元测试。"""
import re, json, unicodedata
BRANDS = {  # 规范名: 别名（含中文、繁体、日文）
    "apple": ["apple", "苹果", "蘋果", "アップル"], "samsung": ["samsung", "三星", "サムスン"], "huawei": ["huawei", "华为", "華為"],
    "xiaomi": ["xiaomi", "小米", "redmi", "红米", "紅米"], "sony": ["sony", "索尼", "ソニー"], "oppo": ["oppo"], "vivo": ["vivo"],
    "honor": ["honor", "荣耀", "榮耀"], "oneplus": ["oneplus", "一加"], "google": ["google", "谷歌"], "lenovo": ["lenovo", "联想", "聯想"],
    "dell": ["dell", "戴尔", "戴爾"], "hp": ["hp", "惠普"], "asus": ["asus", "华硕", "華碩"], "nintendo": ["nintendo", "任天堂"],
    "dyson": ["dyson", "戴森"], "bose": ["bose"], "nike": ["nike", "耐克"], "adidas": ["adidas", "阿迪达斯"], "lg": ["lg"],
    "canon": ["canon", "佳能"], "nikon": ["nikon", "尼康"], "dji": ["dji", "大疆"], "anker": ["anker", "安克"], "logitech": ["logitech", "罗技", "羅技"],
}
COLORS = {  # 规范名: 别名
    "black": ["black", "黑", "黑色", "曜石黑", "午夜色", "midnight", "ブラック"], "white": ["white", "白", "白色", "星光色", "starlight", "ホワイト"],
    "silver": ["silver", "银", "銀", "银色", "銀色"], "gold": ["gold", "金", "金色"], "blue": ["blue", "蓝", "藍", "蓝色", "藍色"],
    "red": ["red", "红", "紅", "红色", "紅色"], "green": ["green", "绿", "綠", "绿色", "綠色"], "purple": ["purple", "紫", "紫色"],
    "pink": ["pink", "粉", "粉色"], "gray": ["gray", "grey", "灰", "灰色", "深空灰", "space gray"], "titanium": ["titanium", "钛", "鈦", "原色钛金属", "原色鈦金屬"],
}
CAP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(tb|t|gb|g|mb|ml|l|kg|mah|w|英寸|吋|寸|inch|\")(?![a-z])", re.I)
MODEL_RE = re.compile(r"\b(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)+\b|\b[a-z]{1,4}\d{2,}[a-z0-9]*\b", re.I)
SERIES_RE = re.compile(r"\b(iphone|galaxy|pixel|mate|nova|redmi|xperia|switch|ipad|macbook|airpods|watch|playstation|ps|xbox)\s*([a-z]?\d{1,3}[a-z]{0,3})?(\s*(pro\s*max|pro|max|plus|ultra|mini|lite|air|fe|se|e))?\b", re.I)
NOISE = re.compile(r"(官方|旗舰店|正品|包邮|现货|限时|特价|新品|热卖|官方正品|國行|国行|公司貨|公司货|台灣公司貨|全新|【.*?】|\[.*?\]|\(.*?保固.*?\))", re.I)
IDENT_RE = {"gtin": re.compile(r"\b(\d{12,14})\b"), "asin": re.compile(r"\b(B0[A-Z0-9]{8})\b")}
def nfkc(s): return unicodedata.normalize("NFKC", s or "").lower()
def _find(table, text):
    hits = []
    for canon, al in table.items():
        for a in al:
            if (re.search(r"\b" + re.escape(a) + r"\b", text) if a.isascii() else a in text): hits.append(canon); break
    return hits
STORAGE = {8, 16, 32, 64, 128, 256, 512, 1024, 2048}
CONDITION = {"refurbished": ["福利品", "福利機", "整新", "翻新", "认证翻新", "認證福利", "refurbished", "renewed", "整備済"], "used": ["二手", "中古", "used", "pre-owned", "拆封"],
             "new": []}
def _cap_norm(n, u):
    u = u.lower(); n = float(n)
    if u in ("tb", "t"): return f"{int(n * 1024)}gb"
    if u == "gb" or (u == "g" and int(n) in STORAGE): return f"{int(n)}gb"
    if u == "g": return f"{n:g}g"   # 克重，如 70g 复印纸
    if u in ("英寸", "吋", "寸", "inch", '"'): return f"{n:g}in"
    return f"{n:g}{u}"
def extract(p):
    """返回 {norm, brand, model, series, capacity, color, ids}。"""
    title = p.get("title") or ""; t = nfkc(title); clean = NOISE.sub(" ", t)
    brand = nfkc(p.get("brand") or "")
    bs = _find(BRANDS, clean) if not brand else (_find(BRANDS, brand) or [brand])
    caps = sorted({_cap_norm(n, u) for n, u in CAP_RE.findall(clean)})
    # 手机/存储里 g 常指容量；保留存储类单位与其他单位
    colors = _find(COLORS, clean)
    sm = SERIES_RE.search(clean); series = None
    if sm: series = re.sub(r"\s+", " ", " ".join(x for x in (sm.group(1), sm.group(2), sm.group(4)) if x)).strip()
    model = (p.get("model") or "").lower().replace("-", "") or None
    if not model:
        for m in MODEL_RE.findall(clean):
            mm = m.lower().replace("-", "")
            if not CAP_RE.fullmatch(m) and not re.fullmatch(r"\d+(gb|g|tb|t|ml|w|mah)", mm): model = mm; break
    attrs = p.get("attrs") or {}
    if isinstance(attrs, str):
        try: attrs = json.loads(attrs or "{}")
        except Exception: attrs = {}
    ids = {}
    for k in ("gtin", "ean", "upc", "asin", "jan"):
        v = p.get(k) or attrs.get(k)
        if v: ids["asin" if k == "asin" else "gtin"] = str(v).strip().lstrip("0") if k != "asin" else str(v).strip().upper()
    norm = re.sub(r"[^\w\s.]+", " ", clean); norm = re.sub(r"\s+", " ", norm).strip()
    cond = next((k for k, al in CONDITION.items() if any(a in t for a in al)), "new")   # 用去噪前的标题：成色常写在【】里
    return {"norm": norm, "condition": cond, "brand": bs[0] if bs else None, "model": model, "series": series, "capacity": caps, "color": colors[0] if colors else None, "ids": ids}
