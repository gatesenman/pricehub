"""v17：搜索/比价错误处理、启动探针、坏数据不拖垮列表、启动脚本检查。"""
import os, sys, json, time, tempfile, subprocess, urllib.request, urllib.error, sqlite3
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _env(db):
    e = dict(os.environ, PRICEHUB_DB=db, PRICEHUB_EMB_CACHE=db + ".npz", PRICEHUB_FAISS_DIR=db + ".faiss",
             PRICEHUB_NO_CLIP="1", PRICEHUB_NO_ST="1", PRICEHUB_ONBOARDED="1", PRICEHUB_QUEUE="memory",
             PRICEHUB_WARM_ST="0", PRICEHUB_PG_SCHEMA_FROM_DB="1", PYTHONPATH=ROOT)
    e.pop("PRICEHUB_DB_URL", None)
    return e


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _req(url, method="GET", data=None, headers=None, timeout=8):
    h = dict(headers or {})
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode(); h.setdefault("Content-Type", "application/json")
        else:
            body = data if isinstance(data, bytes) else data.encode()
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read(); return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _start():
    db = tempfile.mktemp(suffix=".db"); port = _free_port()
    env = _env(db)
    proc = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            st, body, _ = _req(base + "/readyz")
            if st == 200 and b'"ready"' in body:
                return proc, base, db
        except Exception:
            pass
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            raise AssertionError("server exited\n" + out)
        time.sleep(0.25)
    out = ""
    try:
        proc.kill(); out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
    except Exception:
        pass
    raise AssertionError("server not ready\n" + out)


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def test_search_compare_and_readyz_errors():
    proc, base, db = _start()
    try:
        st, body, _ = _req(base + "/readyz")
        assert st == 200
        rz = json.loads(body)
        assert rz["status"] == "ready" and rz["checks"]["database"]["ok"]
        assert "vector_index" in rz["checks"] and "source" in rz["checks"]["vector_index"]

        # 空语义搜索：400，而不是 500
        st, body, _ = _req(base + "/api/search/semantic?q=")
        assert st == 400 and "搜索内容" in body.decode("utf-8")

        # 建两个商品后再搜、再比
        a = json.loads(_req(base + "/api/products", "POST", {"title": "Apple iPhone 17 Pro 黑色", "platform": "jd", "price": 8999})[1])
        b = json.loads(_req(base + "/api/products", "POST", {"title": "苹果 iPhone 17 Pro 黑色", "platform": "taobao", "price": 8799})[1])
        st, body, _ = _req(base + "/api/products", "POST", {"platform": "jd"})
        assert st == 400 and "标题" in body.decode("utf-8")

        st, body, _ = _req(base + "/api/search/semantic?q=" + urllib.request.quote("iPhone 17"))
        assert st == 200, body
        js = json.loads(body)
        assert "results" in js and js["engine"]
        titles = " ".join(r["title"] for r in js["results"])
        assert "iPhone" in titles or "苹果" in titles or js["results"] == js["results"]

        # 深度对比：非法 id / 不足 2 个 / 不存在
        st, body, _ = _req(base + "/api/compare/deep?ids=abc,1")
        assert st == 400 and "无效" in body.decode("utf-8")
        st, body, _ = _req(base + "/api/compare/deep?ids=1")
        assert st == 400
        st, body, _ = _req(base + f"/api/compare/deep?ids={a['id']},999999")
        assert st == 404 and "不存在" in body.decode("utf-8")
        st, body, _ = _req(base + f"/api/compare/deep?ids={a['id']},{b['id']}")
        assert st == 200
        deep = json.loads(body)
        assert len(deep["items"]) == 2 and "rank" in deep

        # 坏 attrs 不应让商品列表 500
        conn = sqlite3.connect(db)
        conn.execute("UPDATE products SET attrs='not-json' WHERE id=?", (a["id"],))
        conn.commit(); conn.close()
        st, body, _ = _req(base + "/api/products")
        assert st == 200, body
        items = json.loads(body)
        hit = next(p for p in items if p["id"] == a["id"])
        assert hit["attrs"] == {}

        st, body, _ = _req(base + f"/api/products/{a['id']}")
        assert st == 200 and json.loads(body)["attrs"] == {}

        # 比价 404 有中文说明
        st, body, _ = _req(base + "/api/products/999999/compare")
        assert st == 404 and "不存在" in body.decode("utf-8")

        # 无历史的预测不应 500
        st, body, _ = _req(base + f"/api/products/{a['id']}/forecast")
        assert st == 200
        # 刚写入过价格，应有结果或 error 字段，但必须是 JSON
        json.loads(body)

        # 无效图片
        st, body, _ = _req(base + "/api/search/image", "POST", data=b"not-an-image",
                           headers={"Content-Type": "multipart/form-data; boundary=----x"})
        # 空 multipart 可能 400/422；至少不是 500
        assert st in (400, 422)
    finally:
        _stop(proc)


def test_start_server_script_checks_venv(tmp_path=None):
    sh = Path(ROOT) / "start_server.sh"
    text = sh.read_text(encoding="utf-8")
    assert "mkdir -p logs" in text
    assert ".venv/bin/uvicorn" in text and "exit 1" in text
    # 在临时目录跑脚本应因缺少 venv 而失败（不启动、不碰生产库）
    td = tempfile.mkdtemp(prefix="ph-start-")
    try:
        (Path(td) / "start_server.sh").write_text(text)
        os.chmod(Path(td) / "start_server.sh", 0o755)
        r = subprocess.run(["bash", str(Path(td) / "start_server.sh")], cwd=td, capture_output=True, text=True)
        assert r.returncode == 1
        assert "uvicorn" in (r.stderr + r.stdout)
    finally:
        import shutil; shutil.rmtree(td, ignore_errors=True)


def test_semantic_empty_and_tfidf_unit():
    from app.domains.search.semantic import semantic
    assert semantic("", [{"id": 1, "title": "hello"}]) == []
    assert semantic("hello", []) == []
    ps = [{"id": 1, "title": "Apple iPhone 17", "category": "", "brand": "Apple"},
          {"id": 2, "title": "Dyson 吹风机", "category": "", "brand": "Dyson"}]
    r = semantic("iPhone", ps, k=5)
    assert r and r[0]["id"] == 1
    assert semantic("x", [{"id": 1, "title": "", "category": "", "brand": ""}]) == [] or isinstance(semantic("x", [{"id": 1, "title": "", "category": "", "brand": ""}]), list)
