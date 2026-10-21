"""采集调度器：优先级队列 + 按域名令牌桶限速 + 指数退避重试 + 死信队列。

- 优先级：数字越小越先执行（0 = 用户手动触发，5 = 定时监控，9 = 后台体检）。
- 限速：每个 key（通常是域名）一个令牌桶，默认 1 次/秒、突发 2；被限速的任务延后到可用时间，不占用工作线程。
- 重试：RetryableError 及一般异常按 base * 2^(n-1) * (1±20%) 退避，上限 max_backoff；PermanentError（含插件 PluginError、4xx）不重试。
- 死信：超过最大次数或永久错误写入 dead_letters 表，可在界面/接口重新投递。
- 任务按 kind 注册处理函数（register_handler），任务本身只带可序列化 payload，便于将来换成 Redis/Celery 等分布式队列。
"""
import heapq, threading, time, random, itertools, json, logging, collections
log = logging.getLogger("pricehub.scheduler")
class PermanentError(Exception): pass

class TokenBucket:
    def __init__(self, rate, burst): self.rate, self.burst, self.tokens, self.t = float(rate), float(burst), float(burst), time.monotonic()
    def take(self):
        """可立即执行返回 0，否则返回需要等待的秒数（不扣令牌）。"""
        now = time.monotonic(); self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate); self.t = now
        if self.tokens >= 1: self.tokens -= 1; return 0.0
        return (1 - self.tokens) / self.rate
class Job:
    _ids = itertools.count(1)
    def __init__(self, kind, payload, key, priority, max_attempts):
        self.id = next(Job._ids); self.kind, self.payload, self.key, self.priority, self.max_attempts = kind, payload, key, priority, max_attempts
        self.attempts = 0; self.state = "queued"; self.result = None; self.error = None; self.created = time.time(); self.done = threading.Event(); self.history = []
    def view(self): return {"id": self.id, "kind": self.kind, "key": self.key, "priority": self.priority, "attempts": self.attempts, "state": self.state, "error": self.error, "payload": self.payload}
class Scheduler:
    def __init__(self, workers=4, rate=1.0, burst=2, base_backoff=2.0, max_backoff=300.0, dead_sink=None, on_result=None):
        self.handlers = {}; self.ready = []; self.delayed = []; self.seq = itertools.count(); self.cv = threading.Condition()
        self.buckets = {}; self.rates = {}; self.default_rate, self.default_burst = rate, burst; self.base_backoff, self.max_backoff = base_backoff, max_backoff
        self.workers_n = workers; self.threads = []; self.running = False; self.dead_sink = dead_sink; self.on_result = on_result
        self.stats_c = collections.Counter(); self.jobs = collections.OrderedDict(); self.active = 0
    def register_handler(self, kind, fn, retry_on=(Exception,), permanent_on=(PermanentError,)): self.handlers[kind] = (fn, retry_on, permanent_on)
    def set_rate(self, key, rate, burst=None): self.rates[key] = (rate, burst or max(1, rate * 2)); self.buckets.pop(key, None)
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
        if self.running: return
        self.running = True
        for i in range(self.workers_n):
            t = threading.Thread(target=self._loop, name=f"sched-{i}", daemon=True); t.start(); self.threads.append(t)
    def stop(self):
        with self.cv: self.running = False; self.cv.notify_all()
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
            try: self._run(j)
            finally:
                with self.cv: self.active -= 1
    def backoff(self, attempt): return min(self.max_backoff, self.base_backoff * (2 ** (attempt - 1))) * random.uniform(0.8, 1.2)
    def _run(self, j):
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
    def stats(self):
        with self.cv:
            return {"backend": getattr(self, "backend", "memory"), "workers": self.workers_n, "running": self.running, "active": self.active, "ready": len(self.ready), "delayed": len(self.delayed),
                    "counters": dict(self.stats_c), "rates": {k: {"rps": v[0], "burst": v[1]} for k, v in self.rates.items()}, "default_rate": {"rps": self.default_rate, "burst": self.default_burst},
                    "recent": [j.view() for j in list(self.jobs.values())[-30:]][::-1]}
