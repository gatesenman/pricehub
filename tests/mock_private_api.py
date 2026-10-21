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
