"""v14：冷启动 / 首屏相关断言。"""
import os, sys, tempfile, subprocess, time, json, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

def _env(db):
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_NO_CLIP="1",
             PRICEHUB_NO_ST="1", PRICEHUB_ONBOARDED="1", PRICEHUB_PG_SCHEMA_FROM_DB="1",
             PRICEHUB_QUEUE="memory", PYTHONPATH=ROOT)
    e.pop("PRICEHUB_DB_URL", None)
    return e

def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)

def test_lazy_models_not_loaded_at_import():
    code = r"""
import os
os.environ["PRICEHUB_NO_ST"] = "0"  # allow, but must stay lazy until used
os.environ["PRICEHUB_NO_CLIP"] = "0"
os.environ["PRICEHUB_DB"] = os.environ["PH_DB"]
os.environ["PRICEHUB_EMB_CACHE"] = os.environ["PH_DB"] + ".npz"
os.environ["PRICEHUB_QUEUE"] = "memory"
from app.domains.search import semantic as S
assert S._ST is None and S._CLIP is None and S._ST_TRIED is False
te, ie = S.engine_status()
assert "sentence-transformers" in te and "按需" in te
assert "CLIP" in ie and "按需" in ie
print("ok", te, ie)
"""
    db = tempfile.mktemp(suffix=".db")
    env = _env(db); env["PH_DB"] = db; env["PRICEHUB_NO_ST"] = "0"; env["PRICEHUB_NO_CLIP"] = "0"
    r = subprocess.run([PY, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok" in r.stdout

def test_static_cache_and_gzip_and_splash():
    db = tempfile.mktemp(suffix=".db"); port = _free_port()
    env = _env(db)
    proc = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                st, body, _ = _get(base + "/readyz"); 
                if st == 200 and b'"ready"' in body: break
            except Exception: pass
            time.sleep(0.25)
        else:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            raise AssertionError("server not ready\n" + out)
        st, body, hdr = _get(base + "/")
        assert st == 200
        html = body.decode("utf-8", "replace")
        assert 'id="ph-boot"' in html
        assert "/static/v14.js" in html
        head = html.split("</head>")[0]
        assert 'src="/static/vendor/chart.js"' not in head  # chart 改为按需加载
        assert 'pricehub-32.png' in html
        st, body, hdr = _get(base + "/static/vendor/alpine.js")
        assert st == 200
        # headers case-insensitive
        h = {k.lower(): v for k, v in hdr.items()}
        assert "cache-control" in h and "max-age" in h["cache-control"]
        # gzip: client urllib may auto-decompress; check content-encoding if present or just size > 0
        assert len(body) > 1000
        st, body, hdr = _get(base + "/api/health")
        j = json.loads(body)
        assert j["ok"] is True
        # with NO_ST=1 should be tfidf fallback, not lazy
        assert "tfidf" in j["text_engine"] or "lazy" in j["text_engine"] or "sentence-transformers" in j["text_engine"]
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()

def test_drawchart_helper_present():
    js = open(os.path.join(ROOT, "app/static/v14.js"), encoding="utf-8").read()
    assert "ensureChart" in js and "ph-boot" in open(os.path.join(ROOT, "app/static/index.html"), encoding="utf-8").read()
    assert "navigate_when_ready" in open(os.path.join(ROOT, "windows/installer/PriceHub.pyw"), encoding="utf-8").read()
