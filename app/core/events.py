"""进程内事件总线（发布/订阅）。
主题约定：领域.动作，如 price.recorded、stock.changed、collect.succeeded、collect.failed、alert.fired、notify.sent。
订阅者异常被隔离并计数，不影响发布方；支持通配订阅 "price.*" 与 "*"。
将来可替换为 Redis Streams / NATS，接口保持 publish/subscribe 不变。"""
import threading, time, fnmatch, collections, logging
log = logging.getLogger("pricehub.events")
class EventBus:
    def __init__(self, history=200):
        self._subs = []; self._lock = threading.Lock()
        self.recent = collections.deque(maxlen=history); self.counts = collections.Counter(); self.errors = collections.Counter()
    def subscribe(self, pattern, fn, name=None):
        with self._lock: self._subs.append((pattern, fn, name or getattr(fn, "__name__", "fn")))
        return fn
    def on(self, pattern):
        def deco(fn): return self.subscribe(pattern, fn)
        return deco
    def unsubscribe(self, fn):
        with self._lock: self._subs = [s for s in self._subs if s[1] is not fn]
    def publish(self, topic, **payload):
        self.counts[topic] += 1; self.recent.append({"topic": topic, "ts": time.time(), "keys": sorted(payload)})
        with self._lock: subs = list(self._subs)
        delivered = 0
        for pat, fn, name in subs:
            if pat == "*" or pat == topic or fnmatch.fnmatch(topic, pat):
                try: fn(topic, **payload); delivered += 1
                except Exception as e:
                    self.errors[name] += 1; log.warning("event handler failed", extra={"topic": topic, "handler": name, "error": str(e)})
        return delivered
    def stats(self):
        return {"subscribers": [{"pattern": p, "handler": n} for p, _, n in self._subs], "published": dict(self.counts), "handler_errors": dict(self.errors), "recent": list(self.recent)[-20:]}
bus = EventBus()
