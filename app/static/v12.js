// v12：同款去重——复核队列、批量处理、自动合并阈值、合并记录与撤销、商品页列表
Object.assign(MODS, {dedup: ['copy', '去重建议']});
const V12 = {
  async loadDedup(t) {
    try { const st = t.f.status, sc = t.f.scope;
      const r = await this.api(`/api/dedup/candidates?status=${st}&scope=${sc}`); t.items = r.items; t.counts = r.counts; t.cfg = t.cfg || r.settings; t.sel = {};
      if (t.view === 'log') t.log = (await this.api('/api/dedup/merges')).items; } catch (e) { this.toast(e.message, true); } },
  ddTime(s) { if (!s) return ''; const d = new Date(s.replace(' ', 'T') + 'Z'); return isNaN(d) ? s : d.toLocaleString('zh-CN', {hour12: false}); },
  scopeLabel(s) { return s === 'same_platform' ? '同平台不同商品页' : '跨平台同款'; },
  ddSelIds(t) { return Object.keys(t.sel || {}).filter(k => t.sel[k]).map(Number); },
  async ddScan(t) { t.busy = true; try { const r = await this.post('/api/dedup/scan', {}); this.toast(`扫描 ${r.pairs} 对，新增/更新候选 ${r.candidates} 条` + (r.auto_merged ? `，自动合并 ${r.auto_merged} 条` : '')); } catch (e) { this.toast(e.message, true); } t.busy = false; this.loadDedup(t); },
  async ddMerge(t, c, keep) {
    try { const r = await this.post(`/api/dedup/candidates/${c.id}/merge`, keep ? {canonical_id: keep} : {});
      this.toast(r.message, false, {label: '撤销', run: async () => { await this.post(`/api/dedup/merges/${r.merge_id}/undo`, {}); this.toast('已撤销合并'); this.loadDedup(t); this.refresh && this.refresh(); }});
      this.loadDedup(t); this.refresh && this.refresh(); } catch (e) { this.toast(e.message, true); } },
  async ddNotSame(t, c) { try { await this.post(`/api/dedup/candidates/${c.id}/not_same`, {}); this.toast('已标记为不是同款，之后不再提示'); this.loadDedup(t); } catch (e) { this.toast(e.message, true); } },
  async ddReopen(t, c) { try { await this.post(`/api/dedup/candidates/${c.id}/reopen`, {}); this.loadDedup(t); } catch (e) { this.toast(e.message, true); } },
  async ddBatch(t, action) {
    const ids = this.ddSelIds(t); if (!ids.length) return this.toast('请先勾选候选', true);
    try { const r = await this.post('/api/dedup/batch', {action, ids}); this.toast(r.message); this.loadDedup(t); this.refresh && this.refresh(); } catch (e) { this.toast(e.message, true); } },
  ddSelectAll(t, v) { const s = {}; (t.items || []).forEach(c => { if (c.status === 'pending') s[c.id] = v; }); t.sel = s; },
  async ddSaveCfg(t) { try { t.cfg = await this.post('/api/dedup/settings', t.cfg, 'PUT'); this.toast('已保存去重设置'); } catch (e) { this.toast(e.message, true); } },
  async ddUndo(t, m) { try { const r = await this.post(`/api/dedup/merges/${m.id}/undo`, {}); this.toast(r.message); this.loadDedup(t); this.refresh && this.refresh(); } catch (e) { this.toast(e.message, true); } },
  async ddView(t, v) { t.view = v; if (v === 'log') t.log = (await this.api('/api/dedup/merges')).items; },
};
(() => { const base = App; window.App = function () {
  const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V12));
  const lt = b.loadTab, om = b.openMod, lp = b.loadProductV5;
  b.loadTab = async function (t) { if (t.type === 'dedup') { await this.loadDedup(t); return; } await lt.call(this, t); };
  b.openMod = function (type, focus = true) {
    if (type !== 'dedup') return om.call(this, type, focus);
    const ex = this.tabs.find(t => t.type === type); if (ex) { if (focus) this.active = ex.id; this.loadDedup(ex); return ex; }
    return this.newTab(type, {items: [], counts: {}, cfg: null, sel: {}, f: {status: 'pending', scope: ''}, view: 'queue', log: [], busy: false}, focus); };
  if (lp) b.loadProductV5 = async function (t) { await lp.call(this, t); try { t.listings = (await this.api(`/api/products/${t.pid}/listings`)).listings; } catch (e) { t.listings = []; } };
  return b;
}; App.confBadge = base.confBadge; App.money = base.money; })();
