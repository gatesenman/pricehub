const charts = {};
function drawChart(id, cfg) { const el = document.getElementById(id); if (!el) return; if (charts[id]) charts[id].destroy(); charts[id] = new Chart(el, cfg); }
const PALETTE = ['#6366f1','#22c55e','#f59e0b','#ef4444','#06b6d4','#a855f7','#84cc16','#ec4899','#64748b','#14b8a6'];
function App() { return {
  menu: [['dashboard','仪表盘','📊'],['products','商品监控','📦'],['compare','比价','⚖️'],['pricing','定价','💹'],['search','搜索','🔍'],['ai','AI 搜索','✨'],['sources','数据源管理','🔌'],['catalog','平台目录','🌐'],['alerts','告警','🔔']],
  page: 'dashboard', nav: false, busy: 0, toasts: [], health: {}, dash: {}, all: [], pf: {q:'',platform:'',category:''}, sk: 'id', asc: true, pg: 1, per: 10,
  d: {}, newPrice: '', cost: '', markup: 0.2, pp: {id:'', cost:'', markup:0.2, r:null},
  sm: 'semantic', sq: '', sres: null, serr: '', sengine: '', providers: {}, ap: 'deepseek', aq: '', ares: null,
  srcs: [], adapters: {}, form: null, testRes: null, facets: {countries:[],types:[]}, cf: {country:'',type:'',q:''}, cat: {}, alertsList: [],
  async api(url, opt = {}) {
    this.busy++;
    try { const r = await fetch(url, opt); const t = await r.text(); let j; try { j = JSON.parse(t) } catch { j = t }
      if (!r.ok) throw new Error((j && j.detail) || ('请求失败 ' + r.status)); return j; }
    finally { this.busy--; } },
  post(url, body, method = 'POST') { return this.api(url, {method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)}); },
  toast(msg, err = false) { const id = Math.random(); this.toasts.push({id, msg, err}); setTimeout(() => this.toasts = this.toasts.filter(t => t.id !== id), 4000); },
  money(v, c) { return v == null || isNaN(v) ? '—' : (c && c !== 'CNY' ? c + ' ' : '¥') + Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2}); },
  title() { return this.page === 'product' ? '商品详情' : (this.menu.find(m => m[0] === this.page) || [, ''])[1]; },
  go(p) { location.hash = '#/' + p; },
  async init() {
    window.addEventListener('hashchange', () => this.route());
    try { [this.health, this.adapters, this.providers, this.facets] = await Promise.all(['/api/health','/api/adapters','/api/ai/providers','/api/platforms/facets'].map(u => this.api(u))); }
    catch (e) { this.toast('初始化失败：' + e.message, true); }
    await this.refresh(); this.route();
  },
  async refresh() { try { [this.dash, this.all, this.srcs] = await Promise.all([this.api('/api/dashboard'), this.api('/api/products'), this.api('/api/sources')]); } catch (e) { this.toast(e.message, true); } },
  async route() {
    const [p, arg] = location.hash.replace(/^#\/?/, '').split('/'); this.page = p || 'dashboard';
    try {
      if (this.page === 'dashboard') { await this.refresh(); this.$nextTick(() => this.dashCharts()); }
      if (this.page === 'products' || this.page === 'compare') this.all = await this.api('/api/products');
      if (this.page === 'product') await this.loadProduct(arg);
      if (this.page === 'pricing' && !this.pp.id && this.all[0]) this.pp.id = String(this.all[0].id);
      if (this.page === 'sources') this.srcs = await this.api('/api/sources');
      if (this.page === 'catalog' && !this.cat.items) await this.loadCatalog();
      if (this.page === 'alerts') this.alertsList = await this.api('/api/alerts');
    } catch (e) { this.toast(e.message, true); }
  },
  dashCharts() {
    const pie = (id, rows) => drawChart(id, {type: 'doughnut', data: {labels: rows.map(r => r.k), datasets: [{data: rows.map(r => r.n), backgroundColor: PALETTE}]}, options: {maintainAspectRatio: false, plugins: {legend: {position: 'right', labels: {boxWidth: 10, font: {size: 11}}}}}});
    pie('catChart', this.dash.by_category || []);
    drawChart('platChart', {type: 'bar', data: {labels: (this.dash.by_platform||[]).map(r => r.k), datasets: [{label: '商品数', data: (this.dash.by_platform||[]).map(r => r.n), backgroundColor: '#6366f1', borderRadius: 4}]}, options: {maintainAspectRatio: false, indexAxis: 'y', plugins: {legend: {display: false}}}});
  },
  uniq(k) { return [...new Set(this.all.map(p => p[k]).filter(Boolean))].sort(); },
  filtered() { const f = this.pf, q = f.q.toLowerCase();
    return this.all.filter(p => (!q || (p.title + ' ' + (p.brand||'')).toLowerCase().includes(q)) && (!f.platform || p.platform === f.platform) && (!f.category || p.category === f.category))
      .sort((a, b) => { const x = a[this.sk] ?? '', y = b[this.sk] ?? ''; return (x > y ? 1 : x < y ? -1 : 0) * (this.asc ? 1 : -1); }); },
  pages() { return Math.max(1, Math.ceil(this.filtered().length / this.per)); },
  paged() { if (this.pg > this.pages()) this.pg = this.pages(); return this.filtered().slice((this.pg - 1) * this.per, this.pg * this.per); },
  sortBy(k) { this.asc = this.sk === k ? !this.asc : true; this.sk = k; },
  async loadProduct(id) {
    this.d = {};
    const [p, h, f, c, pr] = await Promise.all([`/api/products/${id}`, `/api/products/${id}/history`, `/api/products/${id}/forecast`, `/api/products/${id}/compare`, `/api/products/${id}/pricing`].map(u => this.api(u)));
    this.cost = p.cost || ''; this.d = {p, h, f, c, pr}; this.$nextTick(() => this.priceChart());
  },
  priceChart() { const H = this.d.h.history, F = this.d.f.linear_regression || [], L = H.map(x => x.ts.slice(5, 16));
    drawChart('priceChart', {type: 'line', data: {labels: [...L, ...F.map((_, i) => '预测+' + (i + 1))], datasets: [
      {label: '历史价', data: H.map(x => x.price), borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,.1)', fill: true, tension: .3},
      {label: '线性回归预测', data: [...L.slice(0, -1).map(() => null), H.length ? H[H.length - 1].price : null, ...F], borderColor: '#f43f5e', borderDash: [6, 4], tension: .3, pointRadius: 2}]},
      options: {maintainAspectRatio: false, interaction: {mode: 'index', intersect: false}}}); },
  maxOffer() { return Math.max(...(this.d.c.offers || []).map(o => o.price), 1); },
  async loadPricing() { try { this.d.pr = await this.api(`/api/products/${this.d.p.id}/pricing?markup=${this.markup}` + (this.cost ? `&cost=${this.cost}` : '')); } catch (e) { this.toast(e.message, true); } },
  async addPrice() { if (!this.newPrice) return this.toast('请输入价格', true);
    try { await this.post(`/api/products/${this.d.p.id}/prices`, {price: Number(this.newPrice)}); this.newPrice = ''; this.toast('已记录价格'); await this.loadProduct(this.d.p.id); } catch (e) { this.toast(e.message, true); } },
  groups() { const g = {}; this.all.filter(p => p.last_price != null).forEach(p => (g[p.group_key] = g[p.group_key] || []).push(p));
    return Object.values(g).filter(x => new Set(x.map(p => p.platform)).size > 1).map(x => x.sort((a, b) => a.last_price - b.last_price)); },
  async pricingPage() { if (!this.pp.id) return this.toast('请选择商品', true);
    try { this.pp.r = await this.api(`/api/products/${this.pp.id}/pricing?markup=${this.pp.markup}` + (this.pp.cost ? `&cost=${this.pp.cost}` : '')); } catch (e) { this.toast(e.message, true); } },
  async doSearch() { this.serr = ''; if (!this.sq.trim()) return this.toast('请输入搜索内容', true);
    const q = encodeURIComponent(this.sq), u = {semantic: `/api/search/semantic?q=${q}&min_score=0.15`, keyword: `/api/search/keyword?q=${q}`, category: `/api/search/category?category=${q}`, chain: `/api/search/chain?chain=${q}`}[this.sm];
    try { const r = await this.api(u); this.sengine = r.engine || ''; this.sres = r.results || r; } catch (e) { this.serr = e.message; } },
  async imgSearch(ev) { const f = ev.target.files[0]; if (!f) return; this.serr = ''; const fd = new FormData(); fd.append('file', f);
    try { const r = await this.api('/api/search/image', {method: 'POST', body: fd}); this.sengine = r.engine; this.sres = r.results; } catch (e) { this.serr = e.message; } ev.target.value = ''; },
  async aiSearch() { if (!this.aq.trim()) return this.toast('请输入搜索内容', true);
    try { this.ares = await this.api(`/api/search/ai?provider=${this.ap}&q=${encodeURIComponent(this.aq)}`); } catch (e) { this.toast(e.message, true); } },
  summary(s) { const c = JSON.parse(s.config || '{}'); return c.url || (c.urls || []).join(', ') || c.path || ('platform=' + (c.platform || '')); },
  editSource(s, preset) { this.testRes = null;
    const c = s ? JSON.parse(s.config) : (preset ? preset.c : {platform: ''});
    this.form = {id: s && s.id, name: s ? s.name : (preset ? preset.name : ''), type: s ? s.type : (preset ? preset.type : 'mock'), enabled: s ? !!s.enabled : true, c,
      urls: (c.urls || []).join('\n'), headers: JSON.stringify(c.headers || {}, null, 1), mapping: c.mapping ? JSON.stringify(c.mapping, null, 1) : (s ? '' : '{"title":"name","price":"price","external_id":"id"}')}; },
  buildBody() { const f = this.form, c = {...f.c};
    if (f.type === 'webpage') c.urls = f.urls.split('\n').map(x => x.trim()).filter(Boolean);
    if (f.type === 'rest') { c.headers = JSON.parse(f.headers || '{}'); c.mapping = JSON.parse(f.mapping || '{}'); c.method = c.method || 'GET'; }
    if (f.type === 'csv' && f.mapping.trim()) c.mapping = JSON.parse(f.mapping);
    if (!f.name.trim()) throw new Error('请填写名称');
    return {name: f.name, type: f.type, config: c, enabled: f.enabled ? 1 : 0}; },
  async testSource() { try { const r = await this.post('/api/sources/test', this.buildBody()); this.testRes = r.ok ? r : {err: r.error}; } catch (e) { this.testRes = {err: e.message}; } },
  async saveSource() { try { const b = this.buildBody(); this.form.id ? await this.post('/api/sources/' + this.form.id, b, 'PUT') : await this.post('/api/sources', b);
      this.form = null; this.toast('已保存数据源'); this.srcs = await this.api('/api/sources'); } catch (e) { this.toast(e.message, true); } },
  async delSource(s) { if (!confirm(`确定删除数据源「${s.name}」？`)) return; try { await this.api('/api/sources/' + s.id, {method: 'DELETE'}); this.toast('已删除'); this.srcs = await this.api('/api/sources'); } catch (e) { this.toast(e.message, true); } },
  async fetchSource(s) { try { const r = await this.post(`/api/sources/${s.id}/fetch`, {}); this.toast(`「${s.name}」抓取完成：${r.fetched} 条`); await this.refresh(); } catch (e) { this.toast(e.message, true); } },
  async runMonitor() { try { await this.post('/api/monitor/run', {}); this.toast('监控抓取完成'); await this.refresh(); this.route(); } catch (e) { this.toast(e.message, true); } },
  async loadCatalog() { try { this.cat = await this.api(`/api/platforms?limit=60&country=${encodeURIComponent(this.cf.country)}&type=${encodeURIComponent(this.cf.type)}&q=${encodeURIComponent(this.cf.q)}`); } catch (e) { this.toast(e.message, true); } },
}; }
