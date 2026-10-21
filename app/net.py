"""网络访问：代理池轮换 + 统一请求头。代理在「设置 > 采集」中配置（每行一个，如 http://user:pass@host:port）。"""
import itertools, threading, httpx
_lock = threading.Lock(); _cycle = {"list": None, "it": None}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 PriceHub/0.5"
def proxies():
    from . import db
    r = db.q("SELECT v FROM settings WHERE k='proxies'", one=True)
    import json
    return [p for p in (json.loads(r["v"]) if r else []) if p]
def next_proxy():
    ps = proxies()
    with _lock:
        if ps != _cycle["list"]: _cycle.update(list=ps, it=itertools.cycle(ps) if ps else None)
        return next(_cycle["it"]) if _cycle["it"] else None
def get(url, headers=None, timeout=15):
    h = {"User-Agent": UA, **(headers or {})}; px = next_proxy()
    with httpx.Client(proxy=px, timeout=timeout, follow_redirects=True, headers=h) as c: return c.get(url)
