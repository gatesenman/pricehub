"""领域公共工具（v7 从 app/api/lifecycle.py 下沉；路由在 app/api/lifecycle.py 注册）。"""
import os, re, io, csv, json, time, hashlib, secrets, sqlite3, datetime, smtplib, shutil, tempfile
from email.mime.text import MIMEText
import numpy as np, httpx
from fastapi import HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from app import db, search, render, analytics, confidence as CF
from app.v4 import get_setting, set_setting, submit, _undo_token, explain
from app.main import products, product, HOOKS, sched, HERE

EVID_DIR = os.environ.get("PRICEHUB_EVIDENCE_DIR") or os.path.join(HERE, "..", "data", "evidence")
def _now(): return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
def _latest(pid): return db.q("SELECT * FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)
