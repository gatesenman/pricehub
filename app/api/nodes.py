"""v10 分布式采集节点：节点列表（注册 + 心跳）、排空/恢复、探测任务（验证任务在多节点间分发与租约接管）。"""
import os, socket, time
from fastapi import Body, HTTPException
from app.main import app
from app.api.platform import SCHED
def _probe(p):
    t = float(p.get("sleep") or 0); end = time.time() + min(t, 120)
    while time.time() < end: time.sleep(0.05)
    return {"node": f"{socket.gethostname()}-{os.getpid()}", "tag": p.get("tag"), "region": os.environ.get("PRICEHUB_NODE_REGION", ""), "tags": os.environ.get("PRICEHUB_NODE_TAGS", "")}
SCHED.register_handler("node.probe", _probe)
@app.get("/api/nodes")
def nodes_list():
    st = SCHED.stats(); ns = SCHED.nodes()
    return {"backend": getattr(SCHED, "backend", "memory"), "nodes": ns, "online": sum(1 for n in ns if n.get("status") in ("online", "draining")),
            "queue": {"ready": st.get("ready"), "delayed": st.get("delayed")}, "counters": st.get("counters", {}),
            "lease_s": getattr(SCHED, "lease_s", None), "heartbeat_s": getattr(SCHED, "heartbeat_s", None),
            "hint": None if getattr(SCHED, "backend", "") == "redis" else "当前是内存队列，只有本进程一个节点。设置 PRICEHUB_QUEUE=redis 并启动 python -m app.worker 可加入更多采集节点。"}
@app.post("/api/nodes/{nid}/{cmd}")
def nodes_cmd(nid: str, cmd: str):
    if cmd == "forget": return {"ok": SCHED.forget_node(nid)}
    try: SCHED.node_command(nid, cmd)
    except ValueError as e: raise HTTPException(400, str(e) if str(e) != cmd else "不支持的命令")
    return {"ok": True}
@app.post("/api/nodes/probe")
def nodes_probe(body: dict = Body(None)):
    b = body or {}; n = max(1, min(int(b.get("n") or 4), 50)); tag = b.get("tag") or str(int(time.time()))
    from app.core import routing
    req = routing.normalize(b.get("require")); pl = {"sleep": float(b.get("sleep") or 0.5), "tag": tag}
    if req: pl["_require"] = req
    jobs = [SCHED.submit("node.probe", dict(pl), key=f"probe-{tag}-{i}", priority=1, max_attempts=3) for i in range(n)]
    if b.get("wait"): SCHED.wait(jobs, timeout=float(b.get("timeout") or 60))
    return {"tag": tag, "require": req, "jobs": [j.view() for j in jobs], "results": [j.result for j in jobs]}
@app.get("/api/routing/rules")
def routing_rules():
    from app.core import routing
    return {"rules": [{**r, "desc": routing.describe(r)} for r in routing.load_rules()]}
@app.put("/api/routing/rules")
def routing_rules_put(body: dict = Body(...)):
    from app.core import routing
    rules = routing.save_rules(body.get("rules") or []); return {"rules": [{**r, "desc": routing.describe(r)} for r in rules]}
@app.post("/api/routing/preview")
def routing_preview(body: dict = Body(...)):
    """给定要求，列出当前能接这个任务的节点（界面里编辑规则时预览用）。"""
    from app.core import routing
    req = routing.normalize(body.get("require") or body); ns = [n for n in SCHED.nodes() if n.get("status") in ("online", "idle")]
    ok = [n["id"] for n in ns if routing.match(n, req)]
    return {"desc": routing.describe(req), "matched": ok, "fallback": (req or {}).get("fallback", "any"), "will_fallback": bool(req) and not ok}
