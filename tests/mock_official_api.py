"""本地模拟官方商品接口（仅测试用，不连真实第三方）。"""
from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
app = FastAPI()
def _items(q, page, n=5):
    p = int(page or 1); base = (p - 1) * n
    return [{"id": f"{q}-{base+i}", "title": f"{q} 测试商品 {base+i}", "price": 99.0 + base + i, "currency": "USD",
             "url": f"https://example.test/p/{base+i}", "image": None, "seller": "MockSeller"} for i in range(n)]
@app.get("/paapi5/search")
def amazon(q: str = "headphones", page: str = "1", authorization: str = Header(None)):
    if not authorization: return JSONResponse({"error": "unauthorized"}, 401)
    return {"items": _items("amz-"+q, page), "next": str(int(page)+1) if int(page) < 2 else None}
@app.get("/buy/browse/v1/item_summary/search")
def ebay(q: str = "headphones", page: str = "1"): return {"items": _items("ebay-"+q, page), "next": None}
@app.get("/affiliate/product/query")
def ali(q: str = "headphones", page: str = "1"): return {"items": _items("ali-"+q, page)}
@app.get("/jdunion/goods")
def jd(q: str = "headphones", page: str = "1"): return {"items": _items("jd-"+q, page)}
@app.get("/taobao/items")
def tb(q: str = "headphones", page: str = "1"): return {"items": _items("tb-"+q, page)}
@app.get("/keepa/product")
def keepa(q: str = "headphones", page: str = "1"): return {"items": _items("keepa-"+q, page)}
@app.get("/search.json")
def serp(q: str = "headphones", page: str = "1"): return {"items": _items("serp-"+q, page)}
_FLAKY = {"n": 0}
@app.get("/flaky/search")
def flaky(q: str = "x", page: str = "1", fail: int = 2, code: int = 503):
    """前 fail 次返回错误（带 Retry-After），之后成功；用于重试/熔断测试。"""
    _FLAKY["n"] += 1
    if _FLAKY["n"] <= fail: return JSONResponse({"error": "busy"}, code, headers={"Retry-After": "0.1"})
    return {"items": _items("flaky-"+q, page)}
@app.post("/flaky/reset")
def flaky_reset(): _FLAKY["n"] = 0; return {"ok": True}
