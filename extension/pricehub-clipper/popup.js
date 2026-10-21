const $ = id => document.getElementById(id);
chrome.storage.sync.get({base: "http://127.0.0.1:8800", cat: ""}, v => { $("base").value = v.base; $("cat").value = v.cat; });
$("go").onclick = async () => {
  const base = $("base").value.replace(/\/$/, ""); const cat = $("cat").value;
  await chrome.storage.sync.set({base, cat});
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  $("msg").textContent = "发送中…"; $("msg").className = "";
  try {
    const r = await fetch(base + "/api/quick-monitor", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: tab.url, category: cat || undefined})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    $("msg").textContent = j.task_id ? ("已排队，任务 " + j.task_id) : (j.message || "已提交");
  } catch (e) { $("msg").textContent = "失败：" + e.message + "（确认 PriceHub 在跑，且允许本机扩展访问）"; $("msg").className = "err"; }
};
