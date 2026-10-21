"""官方数据源 API 路由。"""
from fastapi import Body
from app.main import app
from app.domains.official import service as S
@app.get("/api/official/adapters")
def official_list(): return S.catalog()
@app.put("/api/official/adapters/{adapter_id}")
def official_upsert(adapter_id: str, body: dict = Body(...)): return S.upsert(adapter_id, body)
@app.post("/api/official/adapters/{adapter_id}/test")
def official_test(adapter_id: str, body: dict = Body(None)): return S.test(adapter_id, body)
@app.post("/api/official/adapters/{adapter_id}/sync")
def official_sync(adapter_id: str, body: dict = Body(None)): return S.sync(adapter_id, body)
