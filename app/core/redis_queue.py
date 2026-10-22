"""分布式任务队列（Redis Streams 后端），接口与内存调度器 Scheduler 一致：register_handler / submit / wait / stats / set_rate。

- 10 个优先级流 pricehub:q:0..9，消费组 workers；任何进程（API 内嵌或独立 worker）都可以消费。
- 跨进程按域名限速：Redis 里记录每个域名下次可执行时间（Lua 原子操作）。
- 重试：延迟有序集合 pricehub:delayed（分数 = 到期时间），到期后重新投递；死信仍写数据库 dead_letters。
- 任务状态存哈希 pricehub:job:<id>，wait() 轮询状态；结果需可 JSON 序列化。
- Redis 不可用时由 make_scheduler() 回退为内存调度器。
"""
import json, time, threading, socket, os, random, logging
log = logging.getLogger("pricehub.queue")
RL_LUA = """local nxt = tonumber(redis.call('GET', KEYS[1]) or '0'); local now = tonumber(ARGV[1]); local gap = tonumber(ARGV[2])
if now >= nxt then redis.call('SET', KEYS[1], tostring(now + gap), 'EX', 3600); return '0' end
return tostring(nxt - now)"""
from .scheduler import PermanentError
class RJob:
    def __init__(self, s, jid): self.s, self.id = s, int(jid)
    def _h(self): return self.s.r.hgetall(self.s.k(f"job:{self.id}"))
    def __getattr__(self, name):
        if name in ("state", "kind", "key", "error"): return self._h().get(name)
        if name in ("attempts", "priority", "max_attempts"): return int(self._h().get(name) or 0)
        if name == "payload": return json.loads(self._h().get("payload") or "{}")
        if name == "result": v = self._h().get("result"); return json.loads(v) if v else None
        raise AttributeError(name)
    def view(self):
        h = self._h(); return {"id": self.id, "kind": h.get("kind"), "key": h.get("key"), "priority": int(h.get("priority") or 0), "attempts": int(h.get("attempts") or 0),
                               "state": h.get("state"), "error": h.get("error") or None, "worker": h.get("worker"), "reclaimed_from": h.get("reclaimed_from") or None, "payload": json.loads(h.get("payload") or "{}"), "backend": "redis"}
class RedisScheduler:
    backend = "redis"
    def __init__(self, url, workers=4, rate=1.0, burst=3, base_backoff=2.0, max_backoff=300.0, dead_sink=None, on_result=None, prefix="pricehub:", consume=True):
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5); self.r.ping(); self.url = url
        self.prefix, self.handlers, self.workers_n, self.default_rate = prefix, {}, workers, rate
        self.base_backoff, self.max_backoff, self.dead_sink, self.on_result, self.consume = base_backoff, max_backoff, dead_sink, on_result, consume
        self.running = False; self.threads = []; self.consumer = f"{socket.gethostname()}-{os.getpid()}"; self._rl = self.r.register_script(RL_LUA)
        # v10 分布式采集节点：注册、心跳、租约
        self.heartbeat_s = float(os.environ.get("PRICEHUB_HEARTBEAT_S", "5")); self.lease_s = float(os.environ.get("PRICEHUB_LEASE_S", "30"))
        self.node_ttl = max(3 * self.heartbeat_s, 10); self.started_at = time.time(); self._inflight = {}; self._il = threading.Lock()
        self._n = {"done": 0, "failed": 0, "reclaimed": 0}; self.draining = False
        for p in range(10):
            try: self.r.xgroup_create(self.k(f"q:{p}"), "workers", id="0", mkstream=True)
            except Exception as e:
                if "BUSYGROUP" not in str(e): raise
    def k(self, s): return self.prefix + s
    def register_handler(self, kind, fn, retry_on=(Exception,), permanent_on=(PermanentError,)): self.handlers[kind] = (fn, retry_on, permanent_on)
    def set_rate(self, key, rate, burst=None): self.r.hset(self.k("rates"), key, float(rate))
    def submit(self, kind, payload=None, key="default", priority=5, max_attempts=4):
        if kind not in self.handlers: raise KeyError(f"未注册的任务类型：{kind}")
        jid = self.r.incr(self.k("seq")); priority = max(0, min(9, int(priority)))
        self.r.hset(self.k(f"job:{jid}"), mapping={"kind": kind, "payload": json.dumps(payload or {}, ensure_ascii=False), "key": key or "default", "priority": priority,
                                                    "max_attempts": max_attempts, "attempts": 0, "state": "queued", "created": time.time()})
        self.r.expire(self.k(f"job:{jid}"), 7 * 86400); self.r.lpush(self.k("recent"), jid); self.r.ltrim(self.k("recent"), 0, 49)
        self.r.xadd(self.k(f"q:{priority}"), {"id": jid}); self.r.hincrby(self.k("stats"), "submitted", 1)
        if self.consume: self.start()
        return RJob(self, jid)
    def start(self):
        if self.running or not self.consume: return
        self.running = True
        for i in range(self.workers_n):
            t = threading.Thread(target=self._loop, name=f"rq-{i}", daemon=True); t.start(); self.threads.append(t)
        threading.Thread(target=self._mover, name="rq-mover", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="rq-heartbeat", daemon=True).start()
        threading.Thread(target=self._reclaimer, name="rq-reclaim", daemon=True).start()
        self._beat()
    def stop(self):
        self.running = False
        try: self.r.delete(self.k(f"node:{self.consumer}")); self.r.srem(self.k("nodes"), self.consumer); self._remember(status="stopped")
        except Exception: pass
    # ---------------- 节点注册 / 心跳 ----------------
    def _node_info(self):
        with self._il: busy = len(self._inflight)
        return {"id": self.consumer, "host": socket.gethostname(), "pid": os.getpid(), "concurrency": self.workers_n, "busy": busy,
                "done": self._n["done"], "failed": self._n["failed"], "reclaimed": self._n["reclaimed"], "started_at": self.started_at,
                "last_seen": time.time(), "status": "draining" if self.draining else "online", "lease_s": self.lease_s}
    def _remember(self, **extra):
        info = {**self._node_info(), **extra}; self.r.hset(self.k("nodes:last"), self.consumer, json.dumps(info))
    def _beat(self):
        info = self._node_info(); hk = self.k(f"node:{self.consumer}")
        self.r.hset(hk, mapping={k: json.dumps(v) for k, v in info.items()}); self.r.expire(hk, int(self.node_ttl))
        self.r.sadd(self.k("nodes"), self.consumer); self.r.hset(self.k("nodes:last"), self.consumer, json.dumps(info))
        cmd = self.r.hget(self.k("nodes:cmd"), self.consumer)
        if cmd == "drain": self.draining = True
        elif cmd == "resume": self.draining = False
        # 续租：把自己正在执行的流条目 idle 清零，避免被其他节点抢走
        with self._il: items = list(self._inflight.values())
        for stream, eid in items:
            try: self.r.xclaim(stream, "workers", self.consumer, 0, [eid], justid=True)
            except Exception: pass
        for stream, eid in items:
            jid = self.r.xrange(stream, eid, eid)
            if jid: self.r.hset(self.k(f"job:{jid[0][1]['id']}"), "lease_until", time.time() + self.lease_s)
    def _heartbeat(self):
        while self.running:
            time.sleep(self.heartbeat_s)
            try: self._beat()
            except Exception as e: log.warning("heartbeat failed", extra={"error": str(e)})
    def _reclaimer(self):
        """租约过期（持有节点超过 lease_s 未续租，通常是宕机）的任务由存活节点认领重跑。"""
        ms = int(self.lease_s * 1000)
        while self.running:
            time.sleep(max(1.0, self.lease_s / 5))
            if self.draining: continue
            for p in range(10):
                st = self.k(f"q:{p}")
                try: res = self.r.xautoclaim(st, "workers", self.consumer, ms, "0-0", count=5)
                except Exception: continue
                for eid, f in (res[1] if res and len(res) > 1 else []):
                    if not f: continue
                    self._n["reclaimed"] += 1; self.r.hincrby(self.k("stats"), "reclaimed", 1)
                    jid = int(f["id"]); hk = self.k(f"job:{jid}")
                    if self.r.hget(hk, "state") == "running": self.r.hset(hk, "state", "queued")
                    self.r.hset(hk, "reclaimed_from", self.r.hget(hk, "worker") or "")
                    self._exec(st, eid, jid)
    def _exec(self, stream, eid, jid):
        with self._il: self._inflight[threading.get_ident()] = (stream, eid)
        try: self._run(jid)
        finally:
            with self._il: self._inflight.pop(threading.get_ident(), None)
            self.r.xack(stream, "workers", eid)
    def nodes(self):
        now = time.time(); out = []
        for nid, raw in self.r.hgetall(self.k("nodes:last")).items():
            info = json.loads(raw); live = self.r.exists(self.k(f"node:{nid}"))
            if live:
                info = {k: json.loads(v) for k, v in self.r.hgetall(self.k(f"node:{nid}")).items()}
            elif info.get("status") != "stopped": info["status"] = "offline"
            if not live and now - float(info.get("last_seen") or 0) > 86400: self.r.hdel(self.k("nodes:last"), nid); continue
            info["age_s"] = round(now - float(info.get("last_seen") or now), 1); info["cmd"] = self.r.hget(self.k("nodes:cmd"), nid)
            out.append(info)
        return sorted(out, key=lambda x: (x["status"] != "online", x["id"]))
    def node_command(self, nid, cmd):
        if cmd not in ("drain", "resume"): raise ValueError(cmd)
        self.r.hset(self.k("nodes:cmd"), nid, cmd); return True
    def forget_node(self, nid):
        self.r.hdel(self.k("nodes:last"), nid); self.r.hdel(self.k("nodes:cmd"), nid); return True
    def _mover(self):
        while self.running:
            try:
                for jid in self.r.zrangebyscore(self.k("delayed"), "-inf", time.time(), start=0, num=100):
                    if self.r.zrem(self.k("delayed"), jid):
                        pr = self.r.hget(self.k(f"job:{jid}"), "priority") or 5; self.r.xadd(self.k(f"q:{pr}"), {"id": jid})
            except Exception as e: log.warning("mover error", extra={"error": str(e)})
            time.sleep(0.05)
    def _loop(self):
        streams = {self.k(f"q:{p}"): ">" for p in range(10)}
        while self.running:
            if self.draining: time.sleep(0.5); continue
            try: res = self.r.xreadgroup("workers", self.consumer, streams, count=1, block=1000)
            except Exception as e: log.warning("xreadgroup failed", extra={"error": str(e)}); time.sleep(1); continue
            for stream, entries in sorted(res or [], key=lambda x: int(x[0].rsplit(":", 1)[1])):
                for eid, f in entries: self._exec(stream, eid, int(f["id"]))
    def backoff(self, attempt): return min(self.max_backoff, self.base_backoff * (2 ** (attempt - 1))) * random.uniform(0.8, 1.2)
    def _throttle(self, key):
        rate = float(self.r.hget(self.k("rates"), key) or self.default_rate)
        return float(self._rl(keys=[self.k(f"rl:{key}")], args=[time.time(), 1.0 / rate]))
    def _run(self, jid):
        hk = self.k(f"job:{jid}"); h = self.r.hgetall(hk)
        if not h or h.get("state") in ("done", "dead"): return
        wait = self._throttle(h["key"])
        if wait > 0:
            self.r.hset(hk, "state", "throttled"); self.r.hincrby(self.k("stats"), "throttled", 1); self.r.zadd(self.k("delayed"), {jid: time.time() + wait}); return
        fn, retry_on, permanent_on = self.handlers[h["kind"]]; att = self.r.hincrby(hk, "attempts", 1); self.r.hset(hk, mapping={"state": "running", "worker": self.consumer, "lease_until": time.time() + self.lease_s})
        job = RJob(self, jid)
        try:
            res = fn(json.loads(h["payload"])); self.r.hset(hk, mapping={"state": "done", "result": json.dumps(res, ensure_ascii=False, default=str), "error": ""})
            self.r.hincrby(self.k("stats"), "done", 1); self._n["done"] += 1; self._emit(job, "succeeded"); return
        except permanent_on as e: err, perm = e, True
        except retry_on as e: err, perm = e, False
        msg = f"{type(err).__name__}: {err}"; self.r.hset(hk, "error", msg)
        if not perm and att < int(h["max_attempts"]):
            self.r.hset(hk, "state", "retry_wait"); self.r.zadd(self.k("delayed"), {jid: time.time() + self.backoff(att)}); self.r.hincrby(self.k("stats"), "retried", 1); self._emit(job, "retry"); return
        self.r.hincrby(self.k("stats"), "dead", 1); self._n["failed"] += 1
        if self.dead_sink:
            try: self.dead_sink(job)
            except Exception as e: log.error("dead sink failed", extra={"error": str(e)})
        self.r.hset(hk, "state", "dead"); self._emit(job, "dead")
    def _emit(self, j, result):
        if self.on_result:
            try: self.on_result(j, result)
            except Exception: pass
    def wait(self, jobs, timeout=None):
        end = None if timeout is None else time.time() + timeout
        for j in jobs:
            while j.state not in ("done", "dead"):
                if end and time.time() > end: return False
                time.sleep(0.05)
        return True
    def stats(self):
        st = self.r.hgetall(self.k("stats")); ready = 0; consumers = set()
        for p in range(10):
            try:
                for g in self.r.xinfo_groups(self.k(f"q:{p}")): ready += int(g.get("lag") or 0) + int(g.get("pending") or 0)
                for c in self.r.xinfo_consumers(self.k(f"q:{p}"), "workers"):
                    if int(c.get("idle", 10 ** 9)) < 10000: consumers.add(c["name"])
            except Exception: pass
        return {"backend": "redis", "workers": self.workers_n, "running": self.running, "active": 0, "ready": ready, "delayed": self.r.zcard(self.k("delayed")),
                "consumers": sorted(consumers), "counters": {k: int(v) for k, v in st.items()}, "rates": self.r.hgetall(self.k("rates")),
                "default_rate": {"rps": self.default_rate}, "recent": [RJob(self, j).view() for j in self.r.lrange(self.k("recent"), 0, 29)]}
def make_scheduler(**kw):
    """按环境变量选择队列后端：PRICEHUB_QUEUE=redis 且 Redis 可连通时用 Redis Streams，否则回退内存队列。"""
    from .scheduler import Scheduler
    if os.environ.get("PRICEHUB_QUEUE", "memory") == "redis":
        url = os.environ.get("PRICEHUB_REDIS_URL", "redis://127.0.0.1:6379/0")
        try:
            s = RedisScheduler(url, consume=os.environ.get("PRICEHUB_EMBEDDED_WORKER", "1") != "0", prefix=os.environ.get("PRICEHUB_REDIS_PREFIX", "pricehub:"), **kw)
            log.info("queue backend redis", extra={"url": url.split("@")[-1]}); return s
        except Exception as e: log.warning("Redis 不可用，回退内存队列", extra={"error": str(e)})
    s = Scheduler(**kw); s.backend = "memory"; return s
