"""价格置信度：0-1 分值 + 等级(高/中/低) + 依据列表。
因素：数据源/提取方式可靠性、数据新鲜度、与历史/同款中位数偏离、币种与解析完整性；预测用拟合度 R² 与样本数。"""
import statistics, datetime, numpy as np
METHOD = {"api": (0.95, "数据接口直接提供"), "structured": (0.90, "表格文件导入"), "mock": (0.85, "演示数据源"),
          "jsonld": (0.85, "网页内嵌的标准商品数据"), "microdata": (0.80, "网页标注的价格字段"),
          "css": (0.75, "按页面位置规则抓取"), "meta": (0.60, "从网页分享信息中读取"), "regex": (0.45, "从网页文字中猜测价格"),
          "ai": (0.40, "AI 搜索给出的价格（未核实）"), "manual": (0.50, "手动录入")}
def level(c): return "高" if c >= 0.75 else "中" if c >= 0.5 else "低"
def pack(c, basis): c = round(max(0.0, min(1.0, c)), 3); return {"confidence": c, "level": level(c), "basis": basis}
def score_price(price, method, history=(), peers=(), currency="CNY", title="x"):
    base, why = METHOD.get(method or "manual", METHOD["manual"]); c = base; basis = [f"来源：{why}（基础分 {base:.2f}）"]
    if price is None or price <= 0: return pack(0.1, basis + ["价格缺失或非正数"])
    if not currency: c *= 0.9; basis.append("未注明币种，可信度打 9 折")
    if not title: c *= 0.9; basis.append("缺少商品名称，可信度打 9 折")
    for name, ref in (("历史中位数", history), ("同款中位数", peers)):
        ref = [x for x in ref if x]
        if len(ref) >= 2 if name == "历史中位数" else len(ref) >= 1:
            m = statistics.median(ref); dev = abs(price - m) / m if m else 0
            if dev > 0.3: c *= 0.6; basis.append(f"比{name}偏离 {dev:.0%}，明显异常，可信度打 6 折")
            elif dev > 0.15: c *= 0.85; basis.append(f"比{name}偏离 {dev:.0%}，可信度打 85 折")
            else: basis.append(f"与{name}基本一致（相差 {dev:.0%}）")
    return pack(c, basis)
def fresh(stored, ts):
    """读取时叠加新鲜度衰减（ts 为 UTC 'YYYY-MM-DD HH:MM:SS'）。"""
    c, basis = stored.get("confidence") or 0.5, list(stored.get("basis") or [])
    try: age = (datetime.datetime.utcnow() - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600
    except Exception: age = 0
    f, txt = (1.0, "24 小时内") if age < 24 else (0.9, "7 天内") if age < 168 else (0.75, "30 天内") if age < 720 else (0.6, "超过 30 天")
    return pack(c * f, basis + [f"新鲜度：{txt}更新（{age:.0f} 小时前）" + ("" if f == 1 else f"，可信度乘 {f:.2f}")])
def forecast_conf(prices):
    n = len(prices)
    if n < 2: return pack(0.1, ["样本不足 2 条"])
    x = np.arange(n); k, b = np.polyfit(x, prices, 1); pred = k * x + b
    ss = float(((prices - np.mean(prices)) ** 2).sum()); r2 = 1 - float(((prices - pred) ** 2).sum()) / ss if ss else 1.0
    samp = min(1.0, n / 10)
    return pack(max(0.0, r2) * 0.7 + samp * 0.3, [f"走势吻合度 {max(0, r2):.0%}（越高越可信）", f"已有 {n} 条历史价（10 条以上最可靠）"])
def pricing_conf(cost, comp_confs):
    basis, c = [], 0.4
    if cost: c += 0.3; basis.append("有成本数据 +0.30")
    else: basis.append("缺少成本数据")
    if comp_confs:
        avg = sum(comp_confs) / len(comp_confs); c += 0.3 * avg * min(1, len(comp_confs) / 3)
        basis.append(f"{len(comp_confs)} 个竞品价，平均置信度 {avg:.2f}")
    else: basis.append("无竞品价，仅成本加成")
    return pack(c, basis)
