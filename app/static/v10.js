// v10：分布式采集节点状态页（设置 → 采集节点）
Object.assign(MODS, {nodes: ['server-environment', '采集节点']});
const V10 = {
  async loadNodes(t) { try { t.n = await this.api('/api/nodes'); t.err = ''; } catch (e) { t.err = e.message; } },
  async nodeCmd(t, n, cmd) {
    try { await this.post(`/api/nodes/${encodeURIComponent(n.id)}/${cmd}`, {}); this.toast({drain: '已通知节点排空（不再领取新任务）', resume: '已恢复节点', forget: '已移除离线节点记录'}[cmd]); setTimeout(() => this.loadNodes(t), 600); }
    catch (e) { this.toast(e.message, true); } },
  async probeNodes(t) {
    t.probing = true;
    try { const r = await this.post('/api/nodes/probe', {n: 8, sleep: 0.5, wait: true, timeout: 60}); const c = {};
      (r.results || []).forEach(x => { if (x && x.node) c[x.node] = (c[x.node] || 0) + 1; }); t.probe = c; this.toast('探测任务完成：' + Object.entries(c).map(([k, v]) => k + ' ×' + v).join('，')); }
    catch (e) { this.toast(e.message, true); }
    t.probing = false; this.loadNodes(t); },
  nodeAge(s) { if (s == null) return '—'; return s < 60 ? Math.round(s) + ' 秒前' : s < 3600 ? Math.round(s / 60) + ' 分钟前' : Math.round(s / 3600) + ' 小时前'; },
  nodeStatus(st) { return {online: ['在线', 'ok'], draining: ['排空中', 'warn'], offline: ['离线', 'err'], stopped: ['已停止', 'muted'], idle: ['空闲', 'muted']}[st] || [st, '']; },
};
(() => { const base = App; window.App = function () {
  const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V10));
  const lt = b.loadTab, om = b.openMod;
  b.loadTab = async function (t) { if (t.type === 'nodes') { await this.loadNodes(t); return; } await lt.call(this, t); };
  b.openMod = function (type, focus = true) {
    if (type !== 'nodes') return om.call(this, type, focus);
    const ex = this.tabs.find(t => t.type === type); if (ex) { if (focus) this.active = ex.id; return ex; }
    const t = this.newTab(type, {n: null, err: '', probe: null, probing: false}, focus);
    // 每 5 秒刷新一次（标签关闭后停止）
    const iv = setInterval(() => { const cur = this.tabs.find(x => x.id === t.id); if (!cur) return clearInterval(iv); if (this.active === t.id) this.loadNodes(cur); }, 5000);
    return t; };
  return b;
}; App.confBadge = base.confBadge; App.money = base.money; })();
