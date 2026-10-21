"""可配置 AI 搜索 provider（OpenAI 兼容 chat 接口），只返回前 10 条。"""
import os, json, re, httpx
PROVIDERS = {
  "deepseek": dict(url="https://api.deepseek.com/chat/completions", model="deepseek-chat", env="DEEPSEEK_API_KEY"),
  "kimi": dict(url="https://api.moonshot.cn/v1/chat/completions", model="moonshot-v1-8k", env="MOONSHOT_API_KEY"),
  "qwen": dict(url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", model="qwen-plus", env="DASHSCOPE_API_KEY"),
  "perplexity": dict(url="https://api.perplexity.ai/chat/completions", model="sonar", env="PERPLEXITY_API_KEY"),
  "openai": dict(url="https://api.openai.com/v1/chat/completions", model="gpt-4o-mini", env="OPENAI_API_KEY"),
}
def ai_search(provider, query, top=10):
    cfg = PROVIDERS.get(provider)
    if not cfg: return {"ok": False, "error": f"未知 AI 服务商，可选 {list(PROVIDERS)}"}
    url = os.environ.get(f"{provider.upper()}_BASE_URL", cfg["url"]); key = os.environ.get(cfg["env"])
    if not key: return {"ok": False, "degraded": True, "provider": provider, "results": [],
                        "message": f"还没有填写 {provider} 的 API 密钥（{cfg['env']}），AI 搜索已降级为空结果。可在「帮助 > 设置 AI 服务」中填写，保存后立即生效，无需重启。"}
    prompt = (f"搜索商品/价格信息：{query}。仅返回 JSON 数组，最多{top}项，按相关度排名，字段 rank,title,platform,price,url,reason。")
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=60,
                       json={"model": cfg["model"], "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status(); txt = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", txt, re.S); items = json.loads(m.group(0)) if m else []
        return {"ok": True, "provider": provider, "results": items[:top]}
    except Exception as e:
        return {"ok": False, "degraded": True, "provider": provider, "results": [], "message": f"AI 服务暂时连不上（{e}）。请检查密钥是否正确、网络是否可用，稍后再试。"}
