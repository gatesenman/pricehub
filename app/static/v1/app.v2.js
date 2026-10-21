const charts = {};
function drawChart(id, cfg) { const el = document.getElementById(id); if (!el) return; if (charts[id]) charts[id].destroy(); charts[id] = new Chart(el, cfg); }
const PALETTE = ['#3794ff','#3fb950','#d7a33a','#f14c4c','#2bc4c4','#b180d7','#89d185','#e06c9f','#8b8b8b','#4ec9b0'];
const MODS = {dashboard:['📊','仪表盘'],products:['📦','商品监控'],compare:['⚖','比价'],pricing:['💹','定价'],search:['🔍','搜索'],ai:['✨','AI 搜索'],sources:['🔌','数据源管理'],catalog:['🌐','平台目录'],alerts:['🔔','告警']};
function App() { return {
  tabs: [], active: null, seq: 0, sel: null, menuOpen: null, ctx: null, form: null, testRes: null, dlg: null, palette: false, pq: '', pi: 0, quick: '',
  showSide: true, showProps: true, sideW: 230, propsW: 260, drag: null, dragData: null, collapsed: {}, theme: localStorage.getItem('ph-theme') || 'dark',
  busy: 0, task: null, toasts: [], online: true, health: {}, dash: {}, all: [], srcs: [], adapters: {}, providers: {}, facets: {countries: [], types: []}, alertsList: [],
  cols: [{k:'id',label:'ID',w:50,num:1},{k:'title',label:'商品',w:300},{k:'platform',label:'平台',w:120},{k:'category',label:'类目',w:90},{k:'chain',label:'产业链',w:160},{k:'brand',label:'品牌',w:90},
         {k:'last_price',label:'现价',w:100,num:1,fmt:(v,p)=>App.money(v,p.currency)},{k:'cost',label:'成本',w:90,num:1,fmt:v=>App.money(v)}],
  get menus() { return [
    {name:'文件', items:[{label:'新建数据源…',key:'Ctrl+N',run:()=>this.editSource(null)},{label:'导入 CSV 商品…',run:()=>this.importCsv()},{label:'导出商品为 CSV',key:'Ctrl+E',run:()=>this.exportCsv()},'-',
      {label:'关闭标签页',key:'Ctrl+W',run:()=>this.closeTab(this.active)},{label:'关闭全部标签页',run:()=>{this.tabs=[];this.active=null}}]},
    {name:'编辑', items:[{label:'复制选中项名称',key:'Ctrl+Shift+C',run:()=>this.copySel()},'-',{label:'查找商品…',key:'Ctrl+F',run:()=>this.openSearch()},{label:'命令面板…',key:'Ctrl+K',run:()=>this.openPalette()}]},
    {name:'视图', items:[{label:'切换资源管理器',key:'Ctrl+B',run:()=>this.showSide=!this.showSide},{label:'切换属性面板',key:'Ctrl+Alt+B',run:()=>this.showProps=!this.showProps},
      {label:'切换深色/浅色主题',key:'Ctrl+Shift+T',run:()=>this.toggleTheme()},'-',...Object.keys(MODS).map(k=>({label:MODS[k][1],run:()=>this.openMod(k)})),'-',{label:'刷新',key:'F5',run:()=>this.reload()}]},
    {name:'数据源', items:[{label:'数据源管理',run:()=>this.openMod('sources')},{label:'新建数据源…',key:'Ctrl+N',run:()=>this.editSource(null)},{label:'立即抓取全部',key:'Ctrl+R',run:()=>this.runMonitor()},'-',{label:'平台目录（920 平台）',run:()=>this.openMod('catalog')}]},
    {name:'工具', items:[{label:'跨平台比价',run:()=>this.openMod('compare')},{label:'定价模型',run:()=>this.openMod('pricing')},{label:'AI 搜索（Top 10）',run:()=>this.openMod('ai')},{label:'以图搜图',run:()=>this.openSearch('', 'image')},{label:'价格告警',run:()=>this.openMod('alerts')}]},
    {name:'帮助', items:[{label:'键盘快捷键',run:()=>this.showKeys()},{label:'API 文档',run:()=>window.open('/docs')},{label:'关于 PriceHub',run:()=>this.about()}]}]; },
  get toolbar() { return [['📊','仪表盘',()=>this.openMod('dashboard')],['📦','商品监控',()=>this.openMod('products')],['🔍','搜索 (Ctrl+F)',()=>this.openSearch()],['✨','AI 搜索',()=>this.openMod('ai')],'|',
    ['＋','新建数据源 (Ctrl+N)',()=>this.editSource(null)],['⟳','立即抓取全部 (Ctrl+R)',()=>this.runMonitor()],['⇩','导出 CSV',()=>this.exportCsv()],'|',
    ['⚖','比价',()=>this.openMod('compare')],['💹','定价',()=>this.openMod('pricing')],['🌐','平台目录',()=>this.openMod('catalog')],['🔔','告警',()=>this.openMod('alerts')],'|',
    ['◧','切换侧栏 (Ctrl+B)',()=>this.showSide=!this.showSide],['◨','切换属性面板',()=>this.showProps=!this.showProps],['◐','切换主题',()=>this.toggleTheme()],['⌘','命令面板 (Ctrl+K)',()=>this.openPalette()]]; },
  async api(url, opt = {}) { this.busy++;
    try { const r = await fetch(url, opt); const t = await r.text(); let j; try { j = JSON.parse(t) } catch { j = t }
      this.online = true; if (!r.ok) throw new Error((j && j.detail) || ('请求失败 ' + r.status)); return j; }
    catch (e) { if (e instanceof TypeError) this.online = false; throw e; } finally { this.busy--; } },
  post(url, body, method = 'POST') { return this.api(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); },
  toast(msg, err = false) { const id = Math.random(); this.toasts.push({id, msg, err}); setTimeout(() => this.toasts = this.toasts.filter(t => t.id !== id), 4000); },
  money(v, c) { return App.money(v, c); },
  fmtTs(ts) { if (!ts) return '—'; const d = new Date(ts.replace(' ', 'T') + 'Z'); return isNaN(d) ? ts : d.toLocaleString('zh-CN', {hour12: false}).slice(5) + ' (UTC+8)'; },
  async init() {
    document.documentElement.dataset.theme = this.theme;
    try { [this.health, this.adapters, this.providers, this.facets] = await Promise.all(['/api/health','/api/adapters','/api/ai/providers','/api/platforms/facets'].map(u => this.api(u))); } catch (e) { this.toast('初始化失败：' + e.message, true); }
    await this.refresh(); this.openMod('dashboard'); this.openMod('products', false);
    setInterval(() => this.api('/api/dashboard').then(d => this.dash = d).catch(() => {}), 15000);
  },
  async refresh() { try { [this.dash, this.all, this.srcs] = await Promise.all([this.api('/api/dashboard'), this.api('/api/products'), this.api('/api/sources')]); } catch (e) { this.toast(e.message, true); } },
  async reload() { await this.refresh(); const t = this.activeTab(); if (t) this.loadTab(t); this.toast('已刷新'); },
  // ---------- 标签页 ----------
  activeTab() { return this.tabs.find(t => t.id === this.active); },
  newTab(type, extra = {}, focus = true) { const t = {id: 't' + (++this.seq), type, icon: (MODS[type] || ['📄'])[0], title: (MODS[type] || ['', ''])[1], ...extra};
    this.tabs.push(t); const tt = this.tabs[this.tabs.length - 1]; if (focus) this.active = t.id; this.loadTab(tt); return tt; },
  openMod(type, focus = true) { const ex = this.tabs.find(t => t.type === type); if (ex) { if (focus) this.active = ex.id; this.loadTab(ex); return ex; }
    const defaults = {products: {f: {q: '', platform: '', category: ''}, sk: 'id', asc: true, pg: 1, per: 20}, search: {m: 'semantic', q: '', res: null, engine: '', err: ''},
      ai: {p: 'deepseek', q: '', r: null}, pricing: {pid: this.all[0] && this.all[0].id, cost: '', markup: 0.2, r: null}, catalog: {country: '', ptype: '', q: '', cat: {}}};
    return this.newTab(type, JSON.parse(JSON.stringify(defaults[type] || {})), focus); },
  closeTab(id) { const i = this.tabs.findIndex(t => t.id === id); if (i < 0) return; Object.keys(charts).filter(k => k.endsWith('-' + id)).forEach(k => { charts[k].destroy(); delete charts[k]; });
    this.tabs.splice(i, 1); if (this.active === id) this.active = this.tabs.length ? this.tabs[Math.min(i, this.tabs.length - 1)].id : null; },
  async loadTab(t) { try {
    if (t.type === 'dashboard') { this.dash = await this.api('/api/dashboard'); this.$nextTick(() => this.dashCharts(t)); }
    if (t.type === 'products' || t.type === 'compare') this.all = await this.api('/api/products');
    if (t.type === 'sources') this.srcs = await this.api('/api/sources');
    if (t.type === 'catalog' && !t.cat.items) await this.loadCatalog(t);
    if (t.type === 'alerts') this.alertsList = await this.api('/api/alerts');
    if (t.type === 'product' && !t.d) await this.loadProduct(t);
  } catch (e) { this.toast(e.message, true); } },
  openProduct(id) { const ex = this.tabs.find(t => t.type === 'product' && t.pid == id); if (ex) { this.active = ex.id; return; }
    const p = this.all.find(x => x.id == id); this.newTab('product', {pid: id, icon: '🏷', title: p ? p.title : '商品 #' + id, d: null, np: '', cost: '', markup: 0.2}); },
  async loadProduct(t) { const id = t.pid;
    try { const [p, h, f, c, pr] = await Promise.all([`/api/products/${id}`, `/api/products/${id}/history`, `/api/products/${id}/forecast`, `/api/products/${id}/compare`, `/api/products/${id}/pricing`].map(u => this.api(u)));
      t.title = p.title; t.cost = p.cost || ''; t.d = {p, h, f, c, pr}; this.select('product', {...p, last_price: h.stats.last}); this.$nextTick(() => this.priceChart(t)); }
    catch (e) { t.d = {error: e.message}; } },
  // ---------- 图表 ----------
  css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); },
  chartOpts(extra = {}) { const fg = this.css('--muted'), grid = this.css('--grid'); return {maintainAspectRatio: false, animation: false, plugins: {legend: {labels: {color: fg, boxWidth: 10, font: {size: 11}}}},
    scales: {x: {ticks: {color: fg, font: {size: 10}}, grid: {color: grid}}, y: {ticks: {color: fg, font: {size: 10}}, grid: {color: grid}}}, ...extra}; },
  dashCharts(t) { const c = this.dash.by_category || [], p = this.dash.by_platform || [];
    drawChart('cat-' + t.id, {type: 'doughnut', data: {labels: c.map(r => r.k), datasets: [{data: c.map(r => r.n), backgroundColor: PALETTE, borderWidth: 0}]}, options: {maintainAspectRatio: false, animation: false, plugins: {legend: {position: 'right', labels: {color: this.css('--fg'), boxWidth: 10}}}}});
    drawChart('plat-' + t.id, {type: 'bar', data: {labels: p.map(r => r.k), datasets: [{label: '商品数', data: p.map(r => r.n), backgroundColor: '#3794ff'}]}, options: this.chartOpts({indexAxis: 'y', plugins: {legend: {display: false}}})}); },
  priceChart(t) { const H = t.d.h.history, F = t.d.f.linear_regression || [], L = H.map(x => x.ts.slice(5, 16));
    drawChart('pc-' + t.id, {type: 'line', data: {labels: [...L, ...F.map((_, i) => '预测+' + (i + 1))], datasets: [
      {label: '历史价', data: H.map(x => x.price), borderColor: '#3794ff', backgroundColor: 'rgba(55,148,255,.12)', fill: true, tension: .25, pointRadius: 2},
      {label: '线性回归预测', data: [...L.slice(0, -1).map(() => null), H.length ? H[H.length - 1].price : null, ...F], borderColor: '#f14c4c', borderDash: [5, 4], pointRadius: 2}]},
      options: this.chartOpts({interaction: {mode: 'index', intersect: false}})}); },
  redrawCharts() { this.tabs.forEach(t => { if (t.type === 'dashboard') this.dashCharts(t); if (t.type === 'product' && t.d && t.d.p) this.priceChart(t); }); },
  statRows(t) { const s = t.d.h.stats, f = t.d.f; return [['记录数', s.count], ['最低价', this.money(s.min)], ['最高价', this.money(s.max)], ['均价', this.money(s.avg)], ['涨跌幅', (s.change_pct ?? 0) + '%'],
    ['移动平均', this.money(f.moving_average)], ['指数平滑', this.money(f.exp_smoothing)], ['日斜率', f.slope ?? '—'], ['7期后预测', this.money((f.linear_regression || []).slice(-1)[0])]]; },
  // ---------- 商品表 ----------
  uniq(k) { return [...new Set(this.all.map(p => p[k]).filter(Boolean))].sort(); },
  filtered(t) { const f = t.f, q = f.q.toLowerCase();
    return this.all.filter(p => (!q || (p.title + ' ' + (p.brand || '')).toLowerCase().includes(q)) && (!f.platform || p.platform === f.platform) && (!f.category || p.category === f.category))
      .sort((a, b) => { const x = a[t.sk] ?? '', y = b[t.sk] ?? ''; return (x > y ? 1 : x < y ? -1 : 0) * (t.asc ? 1 : -1); }); },
  pages(t) { return Math.max(1, Math.ceil(this.filtered(t).length / t.per)); },
  paged(t) { if (t.pg > this.pages(t)) t.pg = this.pages(t); return this.filtered(t).slice((t.pg - 1) * t.per, t.pg * t.per); },
  sortBy(t, k) { t.asc = t.sk === k ? !t.asc : true; t.sk = k; },
  // ---------- 选择/属性/右键 ----------
  select(kind, obj) { this.sel = {kind, obj}; },
  propRows() { const o = this.sel.obj; const M = {product: [['ID','id'],['标题','title'],['平台','platform'],['外部ID','external_id'],['类目','category'],['产业链','chain'],['品牌','brand'],['现价','last_price'],['成本','cost'],['币种','currency'],['同款键','group_key'],['图片哈希','phash'],['链接','url']],
      source: [['ID','id'],['名称','name'],['适配器','type'],['启用','enabled'],['配置','config']], platform: [['平台','name'],['网址','url'],['大洲','region'],['国家','country'],['类型','type'],['范围','scope'],['说明','note']]}[this.sel.kind];
    return M.map(([l, k]) => [l, k === 'last_price' ? this.money(o[k]) : (o[k] ?? '—')]); },
  showCtx(e, p) { this.ctx = {x: Math.min(e.clientX, innerWidth - 200), y: Math.min(e.clientY, innerHeight - 200), items: [
    {label: '在新标签页打开详情', run: () => this.openProduct(p.id)}, {label: '定价建议…', run: () => { const t = this.openMod('pricing'); t.pid = p.id; this.pricingTab(t); }},
    {label: '查找相似商品（语义）', run: () => this.openSearch(p.title, 'semantic', true)}, '-', {label: '复制名称', run: () => this.copy(p.title)}, {label: '复制价格', run: () => this.copy(String(p.last_price))},
    '-', {label: '记录当前价格…', run: () => this.prompt('记录价格', '为「' + p.title + '」录入新价格：', p.last_price, async v => { await this.post(`/api/products/${p.id}/prices`, {price: Number(v)}); this.toast('已记录'); this.refresh(); })}]}; },
  showSrcCtx(e, s) { this.ctx = {x: e.clientX, y: e.clientY, items: [{label: '立即抓取', run: () => this.fetchSource(s)}, {label: '编辑…', run: () => this.editSource(s)}, {label: '复制配置 JSON', run: () => this.copy(s.config)}, '-', {label: '删除', run: () => this.delSource(s)}]}; },
  copy(txt) { (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => this.toast('已复制'), () => this.toast('已复制：' + txt)); },
  copySel() { if (!this.sel) return this.toast('未选择任何项', true); this.copy(this.sel.obj.title || this.sel.obj.name); },
  // ---------- 拖拽 ----------
  dragStart(kind, e, col) { this.drag = kind; this.dragData = {x: e.clientX, w: kind === 'side' ? this.sideW : kind === 'props' ? this.propsW : col.w, col}; },
  dragMove(e) { if (!this.drag) return; const dx = e.clientX - this.dragData.x;
    if (this.drag === 'side') this.sideW = Math.max(150, Math.min(500, this.dragData.w + dx));
    else if (this.drag === 'props') this.propsW = Math.max(180, Math.min(520, this.dragData.w - dx));
    else this.dragData.col.w = Math.max(40, this.dragData.w + dx); },
  dragEnd() { this.drag = null; },
  // ---------- 树 ----------
  toggle(k) { this.collapsed[k] = !this.collapsed[k]; },
  tree() { const cnt = k => this.all.reduce((m, p) => (m[p[k] || '未分类'] = (m[p[k] || '未分类'] || 0) + 1, m), {});
    const pc = cnt('platform'), cc = cnt('category');
    return [{key: 'mod', label: '功能模块', children: Object.keys(MODS).map(k => ({label: MODS[k][1], icon: MODS[k][0], mod: k, run: () => this.openMod(k)}))},
      {key: 'plat', label: '按平台', children: Object.keys(pc).sort().map(k => ({label: k, icon: '🏬', count: pc[k], run: () => this.filterProducts('platform', k)}))},
      {key: 'cat', label: '按类目', children: Object.keys(cc).sort().map(k => ({label: k, icon: '🗂', count: cc[k], run: () => this.filterProducts('category', k)}))}]; },
  isActiveNode(n) { const t = this.activeTab(); return n.mod && t && t.type === n.mod; },
  filterProducts(k, v) { const t = this.openMod('products'); t.f = {q: '', platform: '', category: '', [k]: v}; t.pg = 1; },
  // ---------- 快捷键/命令 ----------
  keys(e) { const c = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (e.key === 'Escape') { this.palette = false; this.ctx = null; this.menuOpen = null; if (!this.form && this.dlg) this.dlg = null; else this.form = null; return; }
    const map = {k: () => this.openPalette(), f: () => this.openSearch(), n: () => this.editSource(null), w: () => this.closeTab(this.active), b: () => e.altKey ? this.showProps = !this.showProps : this.showSide = !this.showSide, r: () => this.runMonitor(), e: () => this.exportCsv()};
    if (c && e.shiftKey && k === 't') { e.preventDefault(); return this.toggleTheme(); }
    if (c && e.shiftKey && k === 'c') { e.preventDefault(); return this.copySel(); }
    if (c && !e.shiftKey && map[k]) { e.preventDefault(); map[k](); }
    if (e.key === 'F5') { e.preventDefault(); this.reload(); }
    if (c && e.key === 'Tab' && this.tabs.length) { e.preventDefault(); const i = this.tabs.findIndex(t => t.id === this.active); this.active = this.tabs[(i + (e.shiftKey ? -1 : 1) + this.tabs.length) % this.tabs.length].id; } },
  openPalette() { this.palette = true; this.pq = ''; this.pi = 0; this.$nextTick(() => this.$refs.pin.focus()); },
  pcmds() { const q = this.pq.trim().toLowerCase(); const cmds = [];
    this.menus.forEach(m => m.items.forEach(it => it !== '-' && cmds.push({label: m.name + ': ' + it.label, key: it.key, run: it.run})));
    this.all.forEach(p => cmds.push({label: '打开商品: ' + p.title, cat: p.platform, run: () => this.openProduct(p.id)}));
    return cmds.filter(c => !q || c.label.toLowerCase().includes(q)).slice(0, 30); },
  runCmd(c) { if (!c) return; this.palette = false; c.run(); },
  toggleTheme() { this.theme = this.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = this.theme; localStorage.setItem('ph-theme', this.theme); this.$nextTick(() => this.redrawCharts()); },
  showKeys() { this.dlg = {title: '键盘快捷键', lines: [['Ctrl+K','命令面板'],['Ctrl+F','查找商品'],['Ctrl+N','新建数据源'],['Ctrl+R','立即抓取全部'],['Ctrl+E','导出 CSV'],['Ctrl+W','关闭标签页'],['Ctrl+Tab','切换标签页'],['Ctrl+B','切换资源管理器'],['Ctrl+Alt+B','切换属性面板'],['Ctrl+Shift+T','切换主题'],['Ctrl+Shift+C','复制选中项'],['F5','刷新'],['Esc','关闭对话框/菜单']].map(r => `<b style="display:inline-block;width:130px">${r[0]}</b>${r[1]}`)}; },
  about() { this.dlg = {title: '关于 PriceHub', lines: ['<b>PriceHub</b> 全球交易平台商品价格全生命周期管理', '文本引擎：' + this.health.text_engine, '图像引擎：' + this.health.image_engine, '适配器：' + (this.health.adapters || []).join(', '), '平台目录：' + this.health.platform_catalog + ' 个']}; },
  prompt(title, line, def, ok) { this.dlg = {title, lines: [line], input: def ?? '', ok}; this.$nextTick(() => { const i = document.querySelector('[data-dlg-input]'); i && i.focus(); }); },
  importCsv() { this.prompt('导入 CSV 商品', '服务器上的 CSV 路径（列：external_id,title,price,cost,category,chain,brand,platform）：', '', async path => {
    try { const r = await this.post('/api/sources', {name: 'CSV导入-' + path.split('/').pop(), type: 'csv', config: {path}}); const f = await this.post(`/api/sources/${r.id}/fetch`, {}); this.toast('已导入 ' + f.fetched + ' 条'); this.refresh(); } catch (e) { this.toast(e.message, true); } }); },
  exportCsv() { const head = ['id','platform','title','category','chain','brand','last_price','cost']; const esc = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const csv = '\ufeff' + [head.join(','), ...this.all.map(p => head.map(h => esc(p[h])).join(','))].join('\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'})); a.download = 'pricehub-products.csv'; a.click(); this.toast('已导出 ' + this.all.length + ' 条商品'); },
  // ---------- 功能 ----------
  async loadPricing(t) { try { t.d.pr = await this.api(`/api/products/${t.d.p.id}/pricing?markup=${t.markup}` + (t.cost ? `&cost=${t.cost}` : '')); } catch (e) { this.toast(e.message, true); } },
  async addPrice(t) { if (!t.np) return this.toast('请输入价格', true);
    try { await this.post(`/api/products/${t.d.p.id}/prices`, {price: Number(t.np)}); t.np = ''; this.toast('已记录价格'); await this.loadProduct(t); } catch (e) { this.toast(e.message, true); } },
  groups() { const g = {}; this.all.filter(p => p.last_price != null).forEach(p => (g[p.group_key] = g[p.group_key] || []).push(p));
    return Object.values(g).filter(x => new Set(x.map(p => p.platform)).size > 1).map(x => x.sort((a, b) => a.last_price - b.last_price)).sort((a, b) => b.length - a.length); },
  async pricingTab(t) { if (!t.pid) return this.toast('请选择商品', true);
    try { t.r = await this.api(`/api/products/${t.pid}/pricing?markup=${t.markup}` + (t.cost ? `&cost=${t.cost}` : '')); } catch (e) { this.toast(e.message, true); } },
  openSearch(q = '', mode, run) { const t = this.openMod('search'); if (mode) t.m = mode; if (q) t.q = q;
    this.$nextTick(() => { const i = document.querySelector('[data-view=search] [data-search-input]'); i && i.focus(); }); if (q && (run || mode == null)) this.doSearch(t); },
  async doSearch(t) { t.err = ''; if (!t.q.trim()) return this.toast('请输入搜索内容', true);
    const q = encodeURIComponent(t.q), u = {semantic: `/api/search/semantic?q=${q}&min_score=0.15`, keyword: `/api/search/keyword?q=${q}`, category: `/api/search/category?category=${q}`, chain: `/api/search/chain?chain=${q}`}[t.m];
    this.task = {label: '搜索中…', p: .5}; try { const r = await this.api(u); t.engine = r.engine || ({keyword: 'SQLite LIKE', category: '类目精确匹配', chain: '产业链前缀匹配'}[t.m]); t.res = r.results || r; } catch (e) { t.err = e.message; } this.task = null; },
  async imgSearch(t, ev) { const f = ev.target.files[0]; if (!f) return; t.err = ''; const fd = new FormData(); fd.append('file', f);
    try { const r = await this.api('/api/search/image', {method: 'POST', body: fd}); t.engine = r.engine; t.res = r.results; } catch (e) { t.err = e.message; } ev.target.value = ''; },
  async aiSearch(t) { if (!t.q.trim()) return this.toast('请输入搜索内容', true);
    try { t.r = await this.api(`/api/search/ai?provider=${t.p}&q=${encodeURIComponent(t.q)}`); } catch (e) { this.toast(e.message, true); } },
  summary(s) { const c = JSON.parse(s.config || '{}'); return c.url || (c.urls || []).join(', ') || c.path || ('platform=' + (c.platform || '')); },
  editSource(s, preset) { this.testRes = null; const c = s ? JSON.parse(s.config) : (preset ? preset.c : {platform: ''});
    this.form = {id: s && s.id, name: s ? s.name : (preset ? preset.name : ''), type: s ? s.type : (preset ? preset.type : 'mock'), enabled: s ? !!s.enabled : true, c,
      urls: (c.urls || []).join('\n'), headers: JSON.stringify(c.headers || {}), mapping: c.mapping ? JSON.stringify(c.mapping) : (s ? '' : '{"title":"name","price":"price","external_id":"id"}')}; },
  buildBody() { const f = this.form, c = {...f.c};
    if (f.type === 'webpage') c.urls = f.urls.split('\n').map(x => x.trim()).filter(Boolean);
    if (f.type === 'rest') { c.headers = JSON.parse(f.headers || '{}'); c.mapping = JSON.parse(f.mapping || '{}'); c.method = c.method || 'GET'; }
    if (f.type === 'csv' && f.mapping.trim()) c.mapping = JSON.parse(f.mapping);
    if (!f.name.trim()) throw new Error('请填写名称'); return {name: f.name, type: f.type, config: c, enabled: f.enabled ? 1 : 0}; },
  async testSource() { try { const r = await this.post('/api/sources/test', this.buildBody()); this.testRes = r.ok ? r : {err: r.error}; } catch (e) { this.testRes = {err: e.message}; } },
  async saveSource() { try { const b = this.buildBody(); this.form.id ? await this.post('/api/sources/' + this.form.id, b, 'PUT') : await this.post('/api/sources', b);
    this.form = null; this.toast('已保存数据源'); this.srcs = await this.api('/api/sources'); this.openMod('sources'); } catch (e) { this.toast(e.message, true); } },
  delSource(s) { this.dlg = {title: '确认删除', lines: [`确定删除数据源「${s.name}」？已抓取的商品与历史价会保留。`], ok: async () => {
    try { await this.api('/api/sources/' + s.id, {method: 'DELETE'}); this.toast('已删除'); this.srcs = await this.api('/api/sources'); } catch (e) { this.toast(e.message, true); } }}; },
  async fetchSource(s) { this.task = {label: '抓取「' + s.name + '」', p: .5};
    try { const r = await this.post(`/api/sources/${s.id}/fetch`, {}); this.toast(`「${s.name}」抓取完成：${r.fetched} 条`); await this.refresh(); } catch (e) { this.toast(e.message, true); } this.task = null; },
  async runMonitor() { const list = (await this.api('/api/sources')).filter(s => s.enabled); let ok = 0;
    for (let i = 0; i < list.length; i++) { this.task = {label: `抓取 ${i + 1}/${list.length}：${list[i].name}`, p: i / list.length};
      try { await this.post(`/api/sources/${list[i].id}/fetch`, {}); ok++; } catch (e) { this.toast(list[i].name + '：' + e.message, true); } }
    this.task = null; this.toast(`监控抓取完成：${ok}/${list.length} 个数据源成功`); await this.refresh(); const t = this.activeTab(); if (t) this.loadTab(t); },
  async loadCatalog(t) { try { t.cat = await this.api(`/api/platforms?limit=200&country=${encodeURIComponent(t.country)}&type=${encodeURIComponent(t.ptype)}&q=${encodeURIComponent(t.q)}`); } catch (e) { this.toast(e.message, true); } },
}; }
App.money = (v, c) => v == null || v === '' || isNaN(v) ? '—' : (c && c !== 'CNY' ? c + ' ' : '¥') + Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
