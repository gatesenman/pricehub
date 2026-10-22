"""v12 同款去重 API。"""
from fastapi import Body
from app.main import app
from app.domains.dedup import service as S
app.add_api_route("/api/dedup/candidates", S.candidates, methods=["GET"])
app.add_api_route("/api/dedup/scan", S.scan, methods=["POST"])
app.add_api_route("/api/dedup/batch", S.batch, methods=["POST"])
app.add_api_route("/api/dedup/settings", S.get_settings, methods=["GET"])
app.add_api_route("/api/dedup/settings", S.put_settings, methods=["PUT"])
app.add_api_route("/api/dedup/merges", S.merges, methods=["GET"])
@app.post("/api/dedup/candidates/{cid}/merge")
def dedup_merge(cid: int, body: dict = Body(None)): return S.merge(cid, (body or {}).get("canonical_id"))
@app.post("/api/dedup/candidates/{cid}/not_same")
def dedup_not_same(cid: int): return S.not_same(cid)
@app.post("/api/dedup/candidates/{cid}/reopen")
def dedup_reopen(cid: int): return S.reopen(cid)
@app.post("/api/dedup/merges/{mid}/undo")
def dedup_undo(mid: int): return S.unmerge(mid)
@app.get("/api/products/{pid}/listings")
def product_listings(pid: int): return S.listings(pid)
