"""v5：对 需JS渲染/反爬拦截 平台用无头浏览器复检，结果写回 platforms 表并记录前后对比。"""
import asyncio, json, datetime, collections
from playwright.async_api import async_playwright
from app import db, parsing
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
async def main():
    before={r["k"]:r["n"] for r in db.q("SELECT status k,COUNT(*) n FROM platforms GROUP BY status")}
    rows=db.q("SELECT id,domain,search_url,status FROM platforms WHERE status IN ('需JS渲染','反爬拦截') AND domain<>''")
    by=collections.defaultdict(list)
    for r in rows: by[r["domain"]].append(r)
    res={}; sem=asyncio.Semaphore(8)
    async with async_playwright() as p:
        b=await p.chromium.launch()
        async def one(d):
            async with sem:
                ctx=await b.new_context(user_agent=UA,locale="zh-CN"); pg=await ctx.new_page()
                url=(by[d][0]["search_url"] or "").replace("{q}","phone") or f"https://{d}/"
                try:
                    r=await pg.goto(url,timeout=20000,wait_until="domcontentloaded"); await pg.wait_for_timeout(3000)
                    html=await pg.content(); st,why=parsing.classify(r.status if r else 200,html,pg.url)
                    if st=="需JS渲染": st,why="可达","渲染后正文仍较少："+why
                except Exception as e: st,why="不可达","渲染超时/失败："+type(e).__name__
                res[d]=(st,why); await ctx.close()
        await asyncio.gather(*(one(d) for d in by)); await b.close()
    now=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"); trans=collections.Counter()
    for d,(st,why) in res.items():
        for r in by[d]:
            old=r["status"]
            if st=="不可达": st2,det=old,f"无头渲染复检失败，保留原判定（{why}）"
            else: st2,det=st,"无头浏览器渲染后："+why
            trans[f"{old}->{st2}"]+=1
            db.ex("UPDATE platforms SET status=?,status_detail=?,checked_at=? WHERE id=?",(st2,det,now,r["id"]))
    after={r["k"]:r["n"] for r in db.q("SELECT status k,COUNT(*) n FROM platforms GROUP BY status")}
    out={"domains":len(by),"before":before,"after":after,"transitions":dict(trans),"at":now+"Z"}
    json.dump(out,open("data/render_recheck_summary.json","w"),ensure_ascii=False,indent=1); print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
