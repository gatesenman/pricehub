"""v11 定向派发：节点声明地域与标签，任务声明要求；只有满足要求的节点领取，无匹配节点时按回退规则处理。

节点标签：环境变量 PRICEHUB_NODE_REGION（如 cn / us）、PRICEHUB_NODE_TAGS（逗号分隔，如 residential-proxy,playwright），
或 `python -m app.worker --region cn --tags playwright`。
任务要求放在 payload["_require"]：{"region": "cn" | ["cn","hk"], "tags": [...], "fallback": "any" | "wait" | "fail"}
- any（默认）：没有在线的匹配节点时，任何节点都可执行（结果里标记 fallback）
- wait：一直排队等匹配节点上线
- fail：直接失败进死信
"""
import os, json
FALLBACKS = ("any", "wait", "fail")
def node_labels():
    region = (os.environ.get("PRICEHUB_NODE_REGION") or "").strip().lower()
    tags = sorted({t.strip().lower() for t in (os.environ.get("PRICEHUB_NODE_TAGS") or "").split(",") if t.strip()})
    return {"region": region, "tags": tags}
def normalize(req):
    if not req: return None
    reg = req.get("region"); reg = [reg] if isinstance(reg, str) else list(reg or [])
    reg = [r.strip().lower() for r in reg if r and str(r).strip() and str(r).strip() != "*"]
    tags = req.get("tags") or []; tags = [tags] if isinstance(tags, str) else tags
    tags = sorted({t.strip().lower() for t in tags if t and str(t).strip()})
    fb = (req.get("fallback") or "any").lower(); fb = fb if fb in FALLBACKS else "any"
    if not reg and not tags: return None
    return {"region": reg, "tags": tags, "fallback": fb}
def match(labels, req):
    req = normalize(req)
    if not req: return True
    if req["region"] and (labels.get("region") or "") not in req["region"]: return False
    return set(req["tags"]) <= set(labels.get("tags") or [])
def describe(req):
    req = normalize(req)
    if not req: return "不限"
    parts = []
    if req["region"]: parts.append("地域 " + "/".join(req["region"]))
    if req["tags"]: parts.append("标签 " + "+".join(req["tags"]))
    return "，".join(parts) + {"any": "（无匹配时任意节点执行）", "wait": "（无匹配时等待）", "fail": "（无匹配时失败）"}[req["fallback"]]

# ---- 平台/数据源规则：data 目录下 routing.json（随数据库文件走），规则按顺序匹配，第一条命中生效 ----
def _file():
    from app.core.storage import DB_PATH
    return os.environ.get("PRICEHUB_ROUTING_FILE") or (str(DB_PATH) + ".routing.json")
def load_rules():
    try:
        with open(_file(), encoding="utf-8") as f: return json.load(f).get("rules", [])
    except Exception: return []
def save_rules(rules):
    clean = []
    for r in rules or []:
        m = (r.get("match") or "").strip()
        if not m: continue
        n = normalize(r) or {"region": [], "tags": [], "fallback": "any"}
        clean.append({"match": m, **n, "note": r.get("note") or ""})
    with open(_file(), "w", encoding="utf-8") as f: json.dump({"rules": clean}, f, ensure_ascii=False, indent=1)
    return clean
def require_for_source(s):
    """数据源配置里的 require 优先；否则按规则匹配（match 可写平台域名、数据源类型或 *）。"""
    try: cfg = json.loads(s.get("config") or "{}") if isinstance(s.get("config"), str) else (s.get("config") or {})
    except Exception: cfg = {}
    if cfg.get("require"): return normalize(cfg["require"])
    keys = {str(cfg.get("platform") or "").lower(), str(s.get("type") or "").lower(), "*"}
    for r in load_rules():
        if r["match"].lower() in keys: return normalize(r)
    return None
