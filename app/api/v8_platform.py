"""v8 平台能力：渲染池配置、浏览器扩展下载、pgvector 状态。"""
import os
from fastapi import Body
from fastapi.responses import FileResponse
from app.main import app
from app.domains.collect import render as R
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
@app.get("/api/render/status")
def render_status(): return R.status()
@app.post("/api/render/config")
def render_config(body: dict = Body(...)):
    return R.configure(workers=body.get("workers"), queue_size=body.get("queue_size"))
@app.get("/api/pgvector/status")
def pgvector_status():
    from app.core import pgvector_store as pv
    return pv.status()
@app.get("/downloads/pricehub-clipper.zip")
def download_clipper():
    p = os.path.join(HERE, "app", "static", "downloads", "pricehub-clipper.zip")
    return FileResponse(p, filename="pricehub-clipper.zip", media_type="application/zip")
