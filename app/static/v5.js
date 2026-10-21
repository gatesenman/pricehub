// v5：告警与通知、同款匹配、控价与卖家画像、定价规则与利润模拟、报表与促销日历、设置与集成、商品到手价/买入建议
Object.assign(MODS, {rules: ['bell-dot', '告警与通知'], match: ['link', '同款匹配'], map: ['shield', '控价与卖家'], reports: ['graph', '报表与日历'], admin: ['settings-gear', '设置与集成']});
const V5 = {
  async loadV5(t) {
    const A = u => this.api(u);
    if (t.type === 'rules') { [t.ch, t.ru, t.no] = await Promise.all([A('/api/channels'), A('/api/rules'), A('/api/notifications?limit=50')]); }
    if (t.type === 'match') { t.loading = true; t.sug = await A('/api/match/suggest?threshold=' + (t.th || 0.75)); t.loading = false; }
    if (t.type === 'map') { [t.v, t.ev, t.sel, t.mp] = await Promise.all([A('/api/map/violations'), A('/api/map/evidence'), A('/api/sellers'), A('/api/map/prices')]); }
    if (t.type === 'reports') { [t.r, t.cal, t.sch, t.ch] = await Promise.all([A('/api/reports/summary'), A('/api/promo/calendar?days=365'), A('/api/reports/schedule'), A('/api/channels')]); }
    if (t.type === 'admin') { [t.fx, t.col, t.tok, t.au] = await Promise.all([A('/api/fx'), A('/api/collect/settings'), A('/api/tokens'), A('/api/audit?limit=50')]); t.proxyText = (t.col.proxies || []).join('\n'); }
    if (t.type === 'pricing') { t.pr = await A('/api/pricing/rules'); }
  },
  v5Defaults() { return {rules: {ch: {types: {}, items: []}, ru: {types: {}, items: []}, no: [], nc: {type: 'webhook', name: '', url: '', chat_id: '', events: false}, nr: {type: 'target', value: '', product_id: '', channel_ids: [], cooldown_min: 60}},
    match: {sug: null, th: 0.75}, map: {v: [], ev: [], sel: [], mp: [], nm: {group_key: '', price: ''}}, reports: {r: null, cal: [], sch: {}, ch: {items: []}},
    admin: {fx: {rates: {}}, col: {render: {}}, tok: {items: [], roles: {}}, au: [], fxCur: 'USD', fxVal: '', proxyText: '', newTok: null, ntRole: 'viewer', ntName: ''}}; },
  // ----- 通知渠道与规则 -----
  async addChannel(t) { const n = t.nc; try { await this.post('/api/channels', {name: n.name, type: n.type, url: n.url, config: n.type === 'telegram' ? {chat_id: n.chat_id} : (n.type === 'webhook' && n.events ? {events: ['price_change', 'price_suggestion']} : {})});
      this.toast('已添加通知渠道'); t.nc = {type: 'webhook', name: '', url: '', chat_id: '', events: false}; await this.loadV5(t); } catch (e) { this.toast(e.message, true); } },
  async testChannel(t, c) { try { const r = await this.post(`/api/channels/${c.id}/test`, {}); this.toast(r.ok ? '测试消息已送达：' + c.name : '发送失败：' + r.detail, !r.ok); await this.loadV5(t); } catch (e) { this.toast(e.message, true); } },
  async delChannel(t, c) { await this.api('/api/channels/' + c.id, {method: 'DELETE'}); await this.loadV5(t); },
  async addRule(t) { const r = t.nr; try { await this.post('/api/rules', {type: r.type, value: r.value === '' ? null : Number(r.value), product_id: r.product_id ? Number(r.product_id) : null, channel_ids: r.channel_ids.map(Number), cooldown_min: Number(r.cooldown_min)});
      this.toast('已添加告警规则'); await this.loadV5(t); } catch (e) { this.toast(e.message, true); } },
  async toggleRule(t, r) { await this.post('/api/rules/' + r.id, {enabled: r.enabled ? 0 : 1}, 'PUT'); await this.loadV5(t); },
  async delRule(t, r) { await this.api('/api/rules/' + r.id, {method: 'DELETE'}); await this.loadV5(t); },
  // ----- 同款 -----
  async mergePair(t, s) { try { const r = await this.post('/api/match/merge', {ids: [s.a.id, s.b.id]}); this.undoToast(r); await this.refresh(); await this.loadV5(t); } catch (e) { this.toast(e.message, true); } },
  // ----- 控价 -----
  async setMap(t) { if (!t.nm.group_key) return this.toast('请选择同款商品', true); await this.post('/api/map/prices', {group_key: t.nm.group_key, price: t.nm.price}); this.toast('已设置最低限价'); await this.loadV5(t); },
  async takeEvidence(t, v) { try { const task = await this.post('/api/map/evidence/' + v.id, {}); this.tasksList = [task, ...this.tasksList]; this.trackTask(task.id, x => { if (x.status === '已完成') this.loadV5(t); }); this.toast('正在后台截图取证…'); } catch (e) { this.toast(e.message, true); } },
  groupsList() { const g = {}; this.all.forEach(p => { if (!g[p.group_key]) g[p.group_key] = p.title; }); return Object.entries(g).sort((a, b) => a[1].localeCompare(b[1])); },
  // ----- 定价规则 -----
  async addPricingRule(t) { const n = t.npr; try { const r = await this.post('/api/pricing/rules', {name: n.name, strategy: n.strategy, param: Number(n.param || 0), min_margin: Number(n.min_margin || 0) / 100, round_to: n.round_to, respect_map: n.respect_map ? 1 : 0});
      t.ruleId = r.id; await this.loadV5(t); this.toast('已保存定价规则'); } catch (e) { this.toast(e.message, true); } },
  async simulate(t) { if (!t.ruleId) return this.toast('请先选择定价规则', true); try { t.sim = await this.post('/api/pricing/simulate', {rule_id: Number(t.ruleId), fee_rate: Number(t.fee) / 100, elasticity: Number(t.el), base_volume: Number(t.vol)}); } catch (e) { this.toast(e.message, true); } },
  async pushPricing(t) { try { const r = await this.post('/api/pricing/push', {rule_id: Number(t.ruleId), fee_rate: Number(t.fee) / 100, elasticity: Number(t.el)}); this.toast(`已把 ${r.items} 条调价建议推送到 ${r.channels} 个系统`); } catch (e) { this.toast(e.message, true); } },
  // ----- 报表 -----
  async saveSchedule(t) { try { t.sch = await this.post('/api/reports/schedule', {enabled: t.sch.enabled, hour: Number(t.sch.hour), channel_ids: (t.sch.channel_ids || []).map(Number)}); this.toast(t.sch.enabled ? `已设置每天 ${t.sch.hour}:00 (UTC+8) 推送报告` : '已关闭定时报告'); } catch (e) { this.toast(e.message, true); } },
  async sendReport() { try { const r = await this.post('/api/reports/send', {}); this.toast(r.sent ? `报告已推送到 ${r.sent} 个渠道` : '没有选择推送渠道，请先在下方勾选', !r.sent); } catch (e) { this.toast(e.message, true); } },
  // ----- 设置与集成 -----
  async saveFx(t) { if (!t.fxVal) return; await this.post('/api/fx', {rates: {[t.fxCur]: Number(t.fxVal)}}); this.toast(`已更新汇率：1 ${t.fxCur} = ${t.fxVal} 人民币`); await this.loadV5(t); },
  async saveCollect(t) { try { t.col = await this.post('/api/collect/settings', {render_fallback: t.col.render_fallback, proxies: t.proxyText.split('\n')}); this.toast('已保存采集设置'); } catch (e) { this.toast(e.message, true); } },
  async addToken(t) { try { t.newTok = await this.post('/api/tokens', {name: t.ntName, role: t.ntRole}); await this.loadV5(t); } catch (e) { this.toast(e.message, true); } },
  async delToken(t, k) { await this.api('/api/tokens/' + k.id, {method: 'DELETE'}); await this.loadV5(t); },
  async restoreDb(t, ev) { const f = ev.target.files[0]; if (!f) return; const fd = new FormData(); fd.append('file', f);
    try { const r = await this.api('/api/restore', {method: 'POST', body: fd}); this.toast(r.message); await this.refresh(); } catch (e) { this.toast(e.message, true); } ev.target.value = ''; },
  // ----- 商品详情扩展 -----
  async loadProductV5(t) { try { const [full, adv] = await Promise.all([this.api(`/api/products/${t.pid}/full`), this.api(`/api/products/${t.pid}/advice`).catch(() => null)]);
      t.x = full; t.adv = adv; t.cf = {shipping: full.shipping ?? '', tax_pct: full.tax_rate != null ? full.tax_rate * 100 : '', coupon: full.coupon ?? '', target_price: full.target_price ?? '', map_price: full.map_price ?? ''}; } catch (e) {} },
  async saveCosts(t) { const c = t.cf; try { t.x = await this.post(`/api/products/${t.pid}/costs`, {shipping: c.shipping, tax_rate: c.tax_pct === '' ? '' : Number(c.tax_pct) / 100, coupon: c.coupon, target_price: c.target_price, map_price: c.map_price}, 'PUT');
      this.toast(c.target_price ? '已保存，降到目标价会提醒你' : '已保存'); await this.loadProductV5(t); } catch (e) { this.toast(e.message, true); } },
  verdictCls(v) { return {'现在买': 'ok', '可以买': 'ok', '再等等': 'warn', '观望': 'muted'}[v] || 'muted'; },
};
// 合并进 App：在 v4 的基础上叠加
(() => { const base = App; window.App = function () { const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V5));
  const lt = b.loadTab, lp = b.loadProduct, om = b.openMod, init = b.init;
  b.loadTab = async function (t) { await lt.call(this, t); if (['rules', 'match', 'map', 'reports', 'admin', 'pricing'].includes(t.type)) { try { await this.loadV5(t); } catch (e) { this.toast(e.message, true); } } };
  b.loadProduct = async function (t) { await lp.call(this, t); await this.loadProductV5(t); };
  b.openMod = function (type, focus = true) { const ex = this.tabs.find(t => t.type === type); if (ex || !['rules', 'match', 'map', 'reports', 'admin'].includes(type)) {
      const t = om.call(this, type, focus); if (type === 'pricing' && t && t.npr === undefined) Object.assign(t, {npr: {name: '', strategy: 'beat_lowest', param: 2, min_margin: 10, round_to: '.9', respect_map: true}, ruleId: '', fee: 5, el: 1.5, vol: 100, sim: null, pr: {strategies: {}, items: []}}); return t; }
    return this.newTab(type, JSON.parse(JSON.stringify(this.v5Defaults()[type] || {})), focus); };
  b.init = async function () { await init.call(this); const m = new URLSearchParams(location.search).get('monitor'); if (m) { history.replaceState(null, '', '/'); this.openQuickMonitor(m, true); } };
  return b; }; App.confBadge = base.confBadge; App.money = base.money; })();
