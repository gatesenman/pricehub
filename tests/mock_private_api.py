"""本地模拟第三方私有数据 API（需 X-API-Key）+ 静态商品网页，用于 e2e。"""
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
import io, random
from PIL import Image, ImageDraw
app = FastAPI()
@app.get("/v1/goods")
def goods(x_api_key: str = Header(None)):
    if x_api_key != "secret-123": raise HTTPException(401, "bad key")
    return {"code": 0, "data": {"items": [
        {"sku": "JP-001", "name": "Nintendo Switch OLED 本体", "pricing": {"amount": 1999 + random.randint(-30, 30)}, "cat": "游戏机", "chain": "消费电子>游戏"},
        {"sku": "EN-002", "name": "Wireless Bluetooth Headphones Noise Cancelling", "pricing": {"amount": 599}, "cat": "耳机", "chain": "消费电子>音频"},
        {"sku": "JP-003", "name": "電動歯ブラシ 充電式", "pricing": {"amount": 299}, "cat": "个护", "chain": "家电>个护"}]}}
def img(color, shape):
    im = Image.new("RGB", (128, 128), "white"); d = ImageDraw.Draw(im)
    (d.ellipse if shape == "circle" else d.rectangle)([20, 20, 108, 108], fill=color); b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
@app.get("/img/{name}.png")
def image(name: str):
    return Response(img(*{"red": ("red", "circle"), "blue": ("blue", "rect")}[name]), media_type="image/png")
@app.get("/item/{n}", response_class=HTMLResponse)
def page(n: int):
    return f"""<html><head><title>商品页</title><meta property="og:title" content="Steam 充值卡 100元"><meta property="og:image" content="http://127.0.0.1:8901/img/red.png"></head>
    <body><h1 class="t">Steam 充值卡 100元</h1><span class="price">¥{95 + n}.00</span></body></html>"""
@app.post("/fake-llm/chat/completions")
def fake_llm():
    import json
    items = [{"rank": i + 1, "title": f"iPhone 16 offer {i+1}", "platform": "p", "price": 7000 + i, "url": "", "reason": ""} for i in range(15)]
    return {"choices": [{"message": {"content": "结果：" + json.dumps(items)}}]}
import json as _json
@app.get("/shop/jsonld", response_class=HTMLResponse)
def p_jsonld():
    d = {"@context": "https://schema.org", "@type": "Product", "name": "Sony WH-1000XM5 Headphones", "offers": {"@type": "Offer", "price": "2499.00", "priceCurrency": "CNY"}}
    return f'<html><head><script type="application/ld+json">{_json.dumps(d)}</script></head><body><h1>Sony</h1></body></html>'
@app.get("/shop/microdata", response_class=HTMLResponse)
def p_micro():
    return '<html><head><meta property="og:title" content="Kindle Paperwhite"></head><body><div itemscope itemtype="https://schema.org/Product"><span itemprop="price" content="1068.00">¥1,068</span><meta itemprop="priceCurrency" content="CNY"></div></body></html>'
@app.get("/shop/meta", response_class=HTMLResponse)
def p_meta():
    return '<html><head><meta property="og:title" content="AirPods Pro 2"><meta property="product:price:amount" content="1,799.00"><meta property="product:price:currency" content="CNY"></head><body>x</body></html>'
@app.get("/shop/regex", response_class=HTMLResponse)
def p_regex(): return "<html><head><title>Xbox 手柄</title></head><body><p>到手价 ¥399.00 包邮</p></body></html>"
@app.get("/shop/blocked", response_class=HTMLResponse)
def p_block(): return HTMLResponse("<html><title>Attention Required! | Cloudflare</title><body>Please complete the captcha</body></html>", status_code=403)
@app.get("/shop/spa", response_class=HTMLResponse)
def p_spa(): return '<html><body><div id="root"></div><noscript>You need to enable JavaScript to run this app.</noscript><script src=a.js></script><script src=b.js></script><script src=c.js></script></body></html>'
@app.get("/shop/search", response_class=HTMLResponse)
def p_search(q: str = ""):
    items = [{"@type": "Product", "name": f"{q} 型号{i}", "url": f"http://127.0.0.1:8901/shop/jsonld?i={i}", "offers": {"price": 100 + i * 10, "priceCurrency": "CNY"}} for i in range(4)]
    return f'<html><script type="application/ld+json">{_json.dumps({"@type": "ItemList", "itemListElement": items})}</script></html>'
# ---------- v5：JS 渲染页、库存/多规格页、通知渠道接收端（本地模拟，不发往真实第三方） ----------
from fastapi import Request as _Req
@app.get("/shop/spa-price", response_class=HTMLResponse)
def p_spa_price():
    return """<html><head><title>loading</title></head><body><div id=root></div><noscript>You need to enable JavaScript to run this app.</noscript><script src=a.js></script><script src=b.js></script>
<script>setTimeout(function(){var s=document.createElement('script');s.type='application/ld+json';s.text=JSON.stringify({"@type":"Product","name":"Nintendo Switch OLED","gtin13":"0045496453435","brand":{"name":"Nintendo"},"offers":{"price":"2099.00","priceCurrency":"CNY","availability":"https://schema.org/InStock"}});document.head.appendChild(s);document.getElementById('root').innerHTML='<h1>Nintendo Switch OLED</h1><b>¥2099</b>';},300)</script></body></html>"""
_STOCK = {"state": "in", "price": "399.00"}
@app.post("/shop/stock/set")
def stock_set(state: str = "in", price: str = "399.00"): _STOCK.update(state=state, price=price); return _STOCK
@app.get("/shop/stock", response_class=HTMLResponse)
def p_stock():
    av = "https://schema.org/InStock" if _STOCK["state"] == "in" else "https://schema.org/OutOfStock"
    d = {"@type": "Product", "name": "Steam 充值卡", "sku": "STEAM", "brand": "Valve", "gtin13": "0000000000017",
         "offers": [{"@type": "Offer", "name": "面值 100", "sku": "S100", "price": _STOCK["price"], "priceCurrency": "CNY", "availability": av},
                    {"@type": "Offer", "name": "面值 200", "sku": "S200", "price": "780.00", "priceCurrency": "CNY", "availability": "https://schema.org/InStock"}]}
    return f'<html><head><script type="application/ld+json">{_json.dumps(d, ensure_ascii=False)}</script></head><body>card</body></html>'
@app.get("/shop/usd", response_class=HTMLResponse)
def p_usd():
    d = {"@type": "Product", "name": "Sony WH-1000XM5 Wireless", "gtin13": "4548736132610", "brand": {"name": "Sony"}, "mpn": "WH-1000XM5",
         "offers": {"price": "329.99", "priceCurrency": "USD", "availability": "InStock", "shippingDetails": {"shippingRate": {"value": "15", "currency": "USD"}}}}
    return f'<html><head><script type="application/ld+json">{_json.dumps(d)}</script></head><body>x</body></html>'
HOOKS = {}
@app.post("/hooks/{name}")
async def hook(name: str, req: _Req):
    HOOKS.setdefault(name, []).append(await req.json()); return {"ok": True, "errcode": 0, "code": 0, "StatusCode": 0}
@app.post("/tg/{bot}/sendMessage")
async def tg(bot: str, req: _Req):
    HOOKS.setdefault("telegram", []).append(await req.json()); return {"ok": True}
@app.get("/hooks/{name}")
def hook_list(name: str): return HOOKS.get(name, [])
@app.delete("/hooks")
def hook_clear(): HOOKS.clear(); return {"ok": True}

@app.get("/feed.json")
def feed_json(fail: int = 0):
    """v6：插件 SDK 示例用的 JSON 商品源；fail=1 时返回 503 用于测试重试/死信。"""
    from fastapi.responses import JSONResponse
    if fail: return JSONResponse({"error": "busy"}, status_code=503)
    return {"items": [{"id": "F1", "name": "Feed 测试耳机 Pro", "price": "1,299.00", "brand": "FeedAudio", "category": "耳机", "in_stock": 1, "gtin": "6900000000011"},
                      {"id": "F2", "name": "Feed 测试键盘", "price": 399, "category": "外设"}]}
