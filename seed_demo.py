import httpx, os
c = httpx.Client(base_url="http://127.0.0.1:8800", timeout=60); R = os.path.dirname(os.path.abspath(__file__))
S = [("淘宝mock","mock",{"platform":"taobao.com","seed":1}),("京东mock","mock",{"platform":"jd.com","bias":1.08,"seed":2}),
     ("CSV样例","csv",{"path":R+"/data/sample_products.csv"}),
     ("私有API示例","rest",{"url":"http://127.0.0.1:8901/v1/goods","headers":{"X-API-Key":"secret-123"},"items_path":"data.items","platform":"private-erp",
       "mapping":{"external_id":"sku","title":"name","price":"pricing.amount","category":"cat","chain":"chain"}}),
     ("卡网网页","webpage",{"urls":["http://127.0.0.1:8901/item/3"],"price_selector":".price","platform":"kawang","category":"虚拟商品"})]
for n,t,cfg in S:
    sid=c.post("/api/sources",json={"name":n,"type":t,"config":cfg}).json()["id"]; print(n,c.post(f"/api/sources/{sid}/fetch").json()["fetched"])
pid=c.get("/api/products").json()[0]["id"]
for i,p in enumerate([8299,8199,8099,7999,7950,7899,7850]): c.post(f"/api/products/{pid}/prices",json={"price":p,"ts":f"2026-10-0{i+1} 12:00:00"})
