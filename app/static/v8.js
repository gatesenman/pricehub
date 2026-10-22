// v8：智能预测展示、官方数据源、渲染池与浏览器扩展、pgvector 状态
Object.assign(MODS, {official: ['cloud-download', '官方数据源']});
const V8 = {
  async runBacktest(t) { const pid = t.pid; t.btLoading = pid; let r; try { r = await this.api(`/api/products/${pid}/backtest?horizon=7&folds=4`); } catch (e) { r = {ok: false, reason: e.message}; } r.pid = pid; if (t.pid === pid) { t.bt = r; t.btLoading = null; } },
  async loadV8(t) {
    if (t.type === 'official') { t.items = (await this.api('/api/official/adapters')).items; t.secret = (await this.api('/api/official/adapters')).secret_source; t.edit = null; }
  },
  async saveOfficial(t, a) {
    try {
      const body = {enabled: a.enabled, mock_base_url: a.mock_base_url || '', credentials: {}, config: a.config || {}};
      (a.auth_fields || []).forEach(f => { const v = (a._cred || {})[f.key]; if (v) body.credentials[f.key] = v; });
      const r = await this.post('/api/official/adapters/' + a.id, body, 'PUT'); t.items = r.items; this.toast('已保存 ' + a.name);
    } catch (e) { this.toast(e.message, true); }
  },
  async testOfficial(t, a) {
    try {
      const r = await this.post('/api/official/adapters/' + a.id + '/test', {mock_base_url: a.mock_base_url || undefined});
      a.last_test = {ok: r.ok, error: r.error, count: r.count}; this.toast(r.ok ? (`测连成功，返回 ${r.count} 条`) : ('测连失败：' + r.error), !r.ok);
    } catch (e) { this.toast(e.message, true); }
  },
  async syncOfficial(t, a) {
    try {
      const r = await this.post('/api/official/adapters/' + a.id + '/sync', {mock_base_url: a.mock_base_url || undefined, force: true});
      this.toast(`已同步 ${r.fetched} 条到商品库`); await this.refresh();
    } catch (e) { this.toast(e.message, true); }
  },
  async saveRenderPool(t) {
    try {
      t.col = await this.post('/api/collect/settings', {render_fallback: t.col.render_fallback, proxies: (t.proxyText || '').split('\n'), render_workers: Number(t.col.render_workers || 1), render_queue: Number(t.col.render_queue || 32)});
      this.toast('已保存渲染池配置（并发变更在重启后或首次渲染前生效）');
    } catch (e) { this.toast(e.message, true); }
  },
};
(() => { const base = App; window.App = function () {
  const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V8));
  const lt = b.loadTab, om = b.openMod, lp = b.loadProductV5 || (async () => {});
  b.loadTab = async function (t) {
    if (t.type === 'official') { try { await this.loadV8(t); } catch (e) { this.toast(e.message, true); } return; }
    await lt.call(this, t);
  };
  b.openMod = function (type, focus = true) {
    if (type !== 'official') return om.call(this, type, focus);
    const ex = this.tabs.find(t => t.type === type); if (ex) { if (focus) this.active = ex.id; return ex; }
    return this.newTab(type, {items: [], secret: ''}, focus);
  };
  // 商品详情：预测面板附加字段已由 /advice 返回，模板直接读 t.adv.buy 等
  return b;
}; App.confBadge = base.confBadge; App.money = base.money; })();
