// v10：分布式采集节点状态页（设置 → 采集节点）
Object.assign(MODS, {nodes: ['server-environment', '采集节点']});
const V10 = {
  async loadNodes(t) { try { t.n = await this.api('/api/nodes'); t.err = ''; if (!t.rules) { t.rules = (await this.api('/api/routing/rules')).rules; } } catch (e) { t.err = e.message; } },
  addRule(t) { (t.rules = t.rules || []).push({match: '', region: [], tags: [], fallback: 'any', _region: '', _tags: ''}); },
  ruleText(r, k) { return r['_' + k] != null ? r['_' + k] : (r[k] || []).join(','); },
  async saveRules(t) {
    try { const rules = (t.rules || []).map(r => ({match: r.match, region: this.ruleText(r, 'region').split(',').map(x => x.trim()).filter(Boolean), tags: this.ruleText(r, 'tags').split(',').map(x => x.trim()).filter(Boolean), fallback: r.fallback}));
      t.rules = (await this.post('/api/routing/rules', {rules}, 'PUT')).rules; this.toast('已保存派发规则'); } catch (e) { this.toast(e.message, true); } },
  async probeRouted(t) {
    const q = t.pq || (t.pq = {region: '', tags: '', fallback: 'any'}); t.probing = true;
    try { const r = await this.post('/api/nodes/probe', {n: 6, sleep: 0.3, wait: true, timeout: 60, require: {region: q.region, tags: q.tags.split(',').map(x => x.trim()).filter(Boolean), fallback: q.fallback}});
      const c = {}; (r.results || []).forEach(x => { if (x && x.node) c[x.node] = (c[x.node] || 0) + 1; }); t.probe = c;
      const rt = {}; (r.jobs || []).forEach(j => { rt[j.state] = (rt[j.state] || 0) + 1; });
      t.probeMsg = (r.require ? '要求：' + [q.region && ('地域 ' + q.region), q.tags && ('标签 ' + q.tags)].filter(Boolean).join('，') : '不限') + ' → ' + (Object.keys(c).length ? Object.entries(c).map(([k, v]) => k + ' ×' + v).join('，') : '没有节点执行');
      this.toast(t.probeMsg); }
    catch (e) { this.toast(e.message, true); }
    t.probing = false; this.loadNodes(t); },
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
    const t = this.newTab(type, {n: null, err: '', probe: null, probing: false, rules: null, pq: {region: '', tags: '', fallback: 'any'}, probeMsg: ''}, focus);
    // 每 5 秒刷新一次（标签关闭后停止）
    const iv = setInterval(() => { const cur = this.tabs.find(x => x.id === t.id); if (!cur) return clearInterval(iv); if (this.active === t.id) this.loadNodes(cur); }, 5000);
    return t; };
  return b;
}; App.confBadge = base.confBadge; App.money = base.money; })();
