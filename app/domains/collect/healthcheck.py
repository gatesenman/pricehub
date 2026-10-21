"""平台批量连通性体检：并发 + 全局限速 + 同域名去重。结果写回 platforms 表。"""
import asyncio, time, datetime, httpx, json
from app import db, parsing
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 PriceHub-HealthCheck/0.3", "Accept-Language": "en,zh;q=0.8"}
RANK = {"解析成功": 5, "需JS渲染": 4, "反爬拦截": 3, "可达": 2, "不可达": 1, "域名失效": 0}
class Limiter:
    def __init__(self, rps): self.gap, self.next, self.lock = 1.0 / rps, 0.0, asyncio.Lock()
    async def wait(self):
        async with self.lock:
            now = time.monotonic(); t = max(now, self.next); self.next = t + self.gap
        await asyncio.sleep(max(0, t - now))
async def probe(client, lim, url):
    await lim.wait(); t0 = time.monotonic()
    try:
        r = await client.get(url)
        if r.status_code == 202 and len(r.text) < 2000: return "反爬拦截", "HTTP 202 空响应（WAF 挑战）", 202, int((time.monotonic() - t0) * 1000)
        st, why = parsing.classify(r.status_code, r.text, str(r.url))
        return st, why, r.status_code, int((time.monotonic() - t0) * 1000)
    except httpx.ConnectError as e:
        m = str(e).lower()
        if any(k in m for k in ("name or service not known", "nodename", "getaddrinfo", "no address", "temporary failure in name resolution", "name resolution")): return "域名失效", "DNS 解析失败", None, None
        return "不可达", "连接失败: " + str(e)[:80], None, None
    except httpx.TimeoutException: return "不可达", "超时", None, None
    except httpx.TooManyRedirects: return "反爬拦截", "重定向循环（通常为 Cookie/JS 校验）", None, None
    except Exception as e: return "不可达", type(e).__name__ + ": " + str(e)[:80], None, None
DOH = None
async def dns_ok(domain):
    """用 DNS-over-HTTPS 判定域名是否存在（本机若为 fake-ip 代理 DNS，系统解析无法识别 NXDOMAIN）。"""
    global DOH
    DOH = DOH or httpx.AsyncClient(timeout=8)
    for url in ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"):
        try:
            r = await DOH.get(url, params={"name": domain, "type": "A"}, headers={"accept": "application/dns-json"}); j = r.json()
            if j.get("Status") == 3: return False
            if j.get("Status") == 0: return bool(j.get("Answer"))
        except Exception: continue
    import socket
    try: await asyncio.get_running_loop().getaddrinfo(domain, 443); return True
    except socket.gaierror: return False
    except Exception: return True
async def check_domain(client, lim, domain, search_url):
    if not await dns_ok(domain):
        if not domain.startswith("www.") and await dns_ok("www." + domain): domain = "www." + domain
        else: return "域名失效", "DNS 解析失败（NXDOMAIN/无记录）", None, None
    st, why, code, ms = await probe(client, lim, f"https://{domain}/")
    if st in ("域名失效",) : return st, why, code, ms
    if st == "不可达":
        st2 = await probe(client, lim, f"http://{domain}/")
        if st2[0] not in ("不可达", "域名失效"): st, why, code, ms = st2
        else: return st, why, code, ms
    if search_url and st in ("可达", "解析成功", "需JS渲染"):
        s2 = await probe(client, lim, search_url.replace("{q}", "phone"))
        if RANK.get(s2[0], 0) > RANK.get(st, 0) or s2[0] == "反爬拦截": st, why = s2[0], "搜索页：" + s2[1]
    return st, why, code, ms
async def run(concurrency=40, rps=20, timeout=12, limit=None, only=None, progress=None):
    rows = db.q("SELECT id, domain, search_url, status FROM platforms WHERE domain<>''" + (" AND id IN (%s)" % ",".join(map(str, only)) if only else ""))
    by_dom = {}
    for r in rows: by_dom.setdefault(r["domain"], []).append(r)
    doms = list(by_dom)[:limit] if limit else list(by_dom)
    sem, lim = asyncio.Semaphore(concurrency), Limiter(rps); done = 0
    async with httpx.AsyncClient(headers=UA, timeout=timeout, follow_redirects=True, verify=False) as client:
        async def one(d):
            nonlocal done
            async with sem: res = await check_domain(client, lim, d, by_dom[d][0]["search_url"])
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            for r in by_dom[d]:
                st = res[0]; detail = res[1]
                if r["status"] == "已关停": st, detail = "已关停", f"名录标注已关停；探测：{res[0]}（{res[1]}）"
                db.ex("UPDATE platforms SET status=?, status_detail=?, http_code=?, latency_ms=?, checked_at=? WHERE id=?", (st, detail, res[2], res[3], now, r["id"]))
            done += 1
            if progress: progress(done, len(doms))
            if done % 50 == 0: print(f"  进度 {done}/{len(doms)}", flush=True)
        await asyncio.gather(*(one(d) for d in doms))
    return len(doms)
def stats():
    return db.q("SELECT status k, COUNT(*) n FROM platforms GROUP BY status ORDER BY n DESC")
