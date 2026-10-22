"""v10 分布式采集节点：节点列表（注册 + 心跳）、排空/恢复、探测任务（验证任务在多节点间分发与租约接管）。"""
import os, socket, time
from fastapi import Body, HTTPException
from app.main import app
from app.api.platform import SCHED
def _probe(p):
    t = float(p.get("sleep") or 0); end = time.time() + min(t, 120)
    while time.time() < end: time.sleep(0.05)
    return {"node": f"{socket.gethostname()}-{os.getpid()}", "tag": p.get("tag")}
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
    jobs = [SCHED.submit("node.probe", {"sleep": float(b.get("sleep") or 0.5), "tag": tag}, key=f"probe-{tag}-{i}", priority=1, max_attempts=3) for i in range(n)]
    if b.get("wait"): SCHED.wait(jobs, timeout=float(b.get("timeout") or 60))
    return {"tag": tag, "jobs": [j.view() for j in jobs], "results": [j.result for j in jobs]}
