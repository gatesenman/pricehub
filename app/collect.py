"""统一采集：先用普通请求（快），读不到价格且页面需要脚本时自动改用无头浏览器渲染（慢但能读 SPA）。"""
import json
from . import parsing, net, render
def render_enabled():
    from . import db
    r = db.q("SELECT v FROM settings WHERE k='render_fallback'", one=True)
    return render.ENABLED and (json.loads(r["v"]) if r else True)
def fetch_parse(url, rule=None, lang="zh-CN,en", force_render=False):
    """返回 (商品 dict 或 None, 状态, 说明, HTTP 状态码)。"""
    st, why, code, d = "不可达", "", 0, None
    if not force_render:
        try:
            r = net.get(url, headers={"Accept-Language": lang}); code = r.status_code
            st, why = parsing.classify(r.status_code, r.text)
            d = parsing.parse_product(r.text, url, rule) if st != "反爬拦截" else None
        except Exception as e: st, why = "不可达", type(e).__name__
    if (not d or not d.get("price")) and code != 404 and (force_render or st in ("需JS渲染", "可达", "解析成功", "反爬拦截", "不可达")) and render_enabled():
        try:
            rr = render.render(url); code = rr["status"] or code
            st2, why2 = parsing.classify(rr["status"] or 200, rr["html"])
            d2 = parsing.parse_product(rr["html"], url, rule) if st2 != "反爬拦截" else None
            if d2 and d2.get("price"): d2["rendered"] = 1; return d2, "解析成功", f"无头浏览器渲染后读取（{rr['ms']}ms）", code
            st, why = st2, f"无头浏览器渲染后仍{('被拦截：' + why2) if st2 == '反爬拦截' else '未读到价格：' + why2}"
        except Exception as e: why = (why + "；" if why else "") + f"渲染失败：{e}"
    return d, st, why, code
