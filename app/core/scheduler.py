"""采集调度器：优先级队列 + 按域名令牌桶限速 + 指数退避重试 + 死信队列。

- 优先级：数字越小越先执行（0 = 用户手动触发，5 = 定时监控，9 = 后台体检）。
- 限速：每个 key（通常是域名）一个令牌桶，默认 1 次/秒、突发 2；被限速的任务延后到可用时间，不占用工作线程。
- 重试：RetryableError 及一般异常按 base * 2^(n-1) * (1±20%) 退避，上限 max_backoff；PermanentError（含插件 PluginError、4xx）不重试。
- 死信：超过最大次数或永久错误写入 dead_letters 表，可在界面/接口重新投递。
- 任务按 kind 注册处理函数（register_handler），任务本身只带可序列化 payload，便于将来换成 Redis/Celery 等分布式队列。
"""
import heapq, threading, time, random, itertools, json, logging, collections, contextvars, math
log = logging.getLogger("pricehub.scheduler")
class PermanentError(Exception): pass

class TokenBucket:
    def __init__(self, rate, burst):
        rate,burst=float(rate),float(burst)
        if not math.isfinite(rate) or rate<=0 or not math.isfinite(burst) or burst<1:
            raise ValueError("rate must be positive and burst >= 1")
        self.rate,self.burst,self.tokens,self.t=rate,burst,burst,time.monotonic()
    def take(self):
        """可立即执行返回 0，否则返回需要等待的秒数（不扣令牌）。"""
        now = time.monotonic(); self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate); self.t = now
        if self.tokens >= 1: self.tokens -= 1; return 0.0
        return (1 - self.tokens) / self.rate
class Job:
    _ids = itertools.count(1)
    def __init__(self, kind, payload, key, priority, max_attempts):
        self.id = next(Job._ids); self.kind, self.payload, self.key, self.priority, self.max_attempts = kind, payload, key, priority, max_attempts
        self.context = contextvars.copy_context()
        self.attempts = 0; self.state = "queued"; self.result = None; self.error = None; self.created = time.time(); self.done = threading.Event(); self.history = []
    def view(self): return {"id": self.id, "kind": self.kind, "key": self.key, "priority": self.priority, "attempts": self.attempts, "state": self.state, "error": self.error, "payload": self.payload, "route": getattr(self, "route", None), "route_desc": getattr(self, "route_desc", None)}
def routing_labels():
    from . import routing; return routing.node_labels()
class Scheduler:
    def __init__(self, workers=4, rate=1.0, burst=2, base_backoff=2.0, max_backoff=300.0, dead_sink=None, on_result=None):
        TokenBucket(rate,burst)  # validate before starting any threads
        if int(workers)<1: raise ValueError("workers must be >= 1")
        self.handlers = {}; self.ready = []; self.delayed = []; self.seq = itertools.count(); self.cv = threading.Condition()
        self.buckets = {}; self.rates = {}; self.default_rate, self.default_burst = rate, burst; self.base_backoff, self.max_backoff = base_backoff, max_backoff
        self.workers_n = workers; self.threads = []; self.running = False; self.dead_sink = dead_sink; self.on_result = on_result
        self.stats_c = collections.Counter(); self.jobs = collections.OrderedDict(); self.active = 0
    def register_handler(self, kind, fn, retry_on=(Exception,), permanent_on=(PermanentError,)): self.handlers[kind] = (fn, retry_on, permanent_on)
    def set_rate(self, key, rate, burst=None):
        b=burst if burst is not None else max(1,float(rate)*2)
        TokenBucket(rate,b)
        with self.cv:
            self.rates[key]=(float(rate),float(b)); self.buckets.pop(key,None); self.cv.notify_all()
    def _bucket(self, key):
        if key not in self.buckets: r, b = self.rates.get(key, (self.default_rate, self.default_burst)); self.buckets[key] = TokenBucket(r, b)
        return self.buckets[key]
    def submit(self, kind, payload=None, key="default", priority=5, max_attempts=4):
        if kind not in self.handlers: raise KeyError(f"未注册的任务类型：{kind}")
        j = Job(kind, payload or {}, key or "default", priority, max_attempts)
        with self.cv:
            self.jobs[j.id] = j
            while len(self.jobs) > 2000: self.jobs.popitem(last=False)
            heapq.heappush(self.ready, (priority, next(self.seq), j)); self.stats_c["submitted"] += 1; self.cv.notify()
        self.start(); return j
    def start(self):
        with self.cv:
            if self.running: return
            self.running=True
            self.threads=[t for t in self.threads if t.is_alive()]
            for i in range(max(0,self.workers_n-len(self.threads))):
                t=threading.Thread(target=self._loop,name=f"sched-{i}",daemon=True)
                self.threads.append(t); t.start()
    def stop(self, wait=True, timeout=5):
        with self.cv:
            self.running=False; self.cv.notify_all()
            threads=list(self.threads)
        if wait:
            deadline=time.monotonic()+timeout
            for t in threads:
                if t is not threading.current_thread(): t.join(max(0,deadline-time.monotonic()))
    def _next(self):
        with self.cv:
            while self.running:
                now = time.monotonic()
                while self.delayed and self.delayed[0][0] <= now:
                    _, _, j = heapq.heappop(self.delayed); heapq.heappush(self.ready, (j.priority, next(self.seq), j))
                while self.ready:
                    pr, s, j = heapq.heappop(self.ready); wait = self._bucket(j.key).take()
                    if wait <= 0: self.active += 1; return j
                    j.state = "throttled"; self.stats_c["throttled"] += 1; heapq.heappush(self.delayed, (now + wait, next(self.seq), j))
                timeout = (self.delayed[0][0] - now) if self.delayed else 1.0
                self.cv.wait(max(0.005, min(timeout, 1.0)))
        return None
    def _loop(self):
        while True:
            j = self._next()
            if j is None: return
            try: j.context.run(self._run,j)
            except Exception as e:
                # Malformed routing or unexpected handler exceptions must not kill a worker.
                j.state="dead"; j.error=f"{type(e).__name__}: {e}"
                j.history.append({"attempt":j.attempts,"ok":False,"error":j.error})
                self.stats_c["dead"]+=1
                log.exception("unexpected job failure",extra={"job":j.id})
                if self.dead_sink:
                    try: j.context.run(self.dead_sink,j)
                    except Exception: log.exception("dead sink failed")
                self._emit(j,"dead"); j.done.set()
            finally:
                with self.cv: self.active -= 1
    def backoff(self, attempt): return min(self.max_backoff, self.base_backoff * (2 ** (attempt - 1))) * random.uniform(0.8, 1.2)
    def _run(self, j):
        from . import routing
        req = routing.normalize((j.payload or {}).get("_require") if isinstance(j.payload, dict) else None)
        if req:
            j.route_desc = routing.describe(req)
            if routing.match(routing.node_labels(), req): j.route = "matched"
            elif req["fallback"] == "any": j.route = "fallback"; self.stats_c["fallback"] += 1
            elif req["fallback"] == "wait":
                j.state = "waiting_node"
                with self.cv: heapq.heappush(self.delayed, (time.monotonic() + 2, next(self.seq), j))
                return
            else:
                j.state = "dead"; j.route = "unroutable"; j.error = "没有满足要求的在线节点：" + j.route_desc; self.stats_c["dead"] += 1
                if self.dead_sink:
                    try: self.dead_sink(j)
                    except Exception as e: log.error("dead sink failed", extra={"error": str(e)})
                self._emit(j, "dead"); j.done.set(); return
        fn, retry_on, permanent_on = self.handlers[j.kind]; j.attempts += 1; j.state = "running"; t0 = time.time()
        try:
            j.result = fn(j.payload); j.state = "done"; j.error = None; self.stats_c["done"] += 1
            j.history.append({"attempt": j.attempts, "ok": True, "ms": int((time.time() - t0) * 1000)}); self._emit(j, "succeeded"); j.done.set(); return
        except permanent_on as e: err, permanent = e, True
        except retry_on as e: err, permanent = e, False
        j.error = f"{type(err).__name__}: {err}"; j.history.append({"attempt": j.attempts, "ok": False, "error": j.error})
        if not permanent and j.attempts < j.max_attempts:
            d = self.backoff(j.attempts); j.state = "retry_wait"; self.stats_c["retried"] += 1
            with self.cv: heapq.heappush(self.delayed, (time.monotonic() + d, next(self.seq), j)); self.cv.notify()
            log.info("job retry", extra={"job": j.id, "kind": j.kind, "attempt": j.attempts, "delay_s": round(d, 2), "error": j.error}); self._emit(j, "retry"); return
        j.state = "dead"; self.stats_c["dead"] += 1
        log.warning("job dead", extra={"job": j.id, "kind": j.kind, "key": j.key, "attempts": j.attempts, "error": j.error})
        if self.dead_sink:
            try: self.dead_sink(j)
            except Exception as e: log.error("dead sink failed", extra={"error": str(e)})
        self._emit(j, "dead"); j.done.set()   # 先落死信再唤醒等待方
    def _emit(self, j, result):
        if self.on_result:
            try: self.on_result(j, result)
            except Exception: pass
    def wait(self, jobs, timeout=None):
        end = None if timeout is None else time.time() + timeout
        for j in jobs:
            if not j.done.wait(None if end is None else max(0, end - time.time())): return False
        return True
    def nodes(self):
        """内存队列只有本进程一个节点（无法跨进程分发）；要多节点请用 PRICEHUB_QUEUE=redis。"""
        import socket, os
        c = self.stats_c
        return [{"id": f"{socket.gethostname()}-{os.getpid()}", "host": socket.gethostname(), "pid": os.getpid(), "concurrency": self.workers_n,
                 "busy": self.active, "done": c.get("done", 0), "failed": c.get("dead", 0), "reclaimed": 0, **routing_labels(), "fallback": c.get("fallback", 0), "deferred": 0, "status": "online" if self.running else "idle",
                 "last_seen": time.time(), "age_s": 0, "embedded": True, "lease_s": None, "cmd": None}]
    def node_command(self, nid, cmd): raise ValueError("内存队列不支持远程节点控制，请改用 Redis 队列")
    def forget_node(self, nid): return False
    def stats(self):
        with self.cv:
            return {"backend": getattr(self, "backend", "memory"), "workers": self.workers_n, "running": self.running, "active": self.active, "ready": len(self.ready), "delayed": len(self.delayed),
                    "counters": dict(self.stats_c), "rates": {k: {"rps": v[0], "burst": v[1]} for k, v in self.rates.items()}, "default_rate": {"rps": self.default_rate, "burst": self.default_burst},
                    "recent": [j.view() for j in list(self.jobs.values())[-30:]][::-1]}
