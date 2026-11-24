const charts = {};
function drawChart(id, cfg) { const el = document.getElementById(id); if (!el) return; if (charts[id]) charts[id].destroy(); charts[id] = new Chart(el, cfg); }
const PALETTE = ['#3794ff','#3fb950','#d7a33a','#f14c4c','#2bc4c4','#b180d7','#89d185','#e06c9f','#8b8b8b','#4ec9b0'];
const MODS = {dashboard:['dashboard','仪表盘'],products:['package','商品监控'],compare:['diff','比价'],pricing:['tag','定价'],search:['search','搜索'],ai:['sparkle','AI 搜索'],sources:['plug','数据源管理'],catalog:['globe','平台目录'],alerts:['bell','告警']};
const ESC = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const METHOD_NAME = {api:'数据接口',structured:'表格导入',mock:'演示数据',jsonld:'网页标准商品数据',microdata:'网页价格标注',css:'页面位置规则',meta:'网页分享信息',regex:'网页文字识别',ai:'AI 搜索',manual:'手动录入'};
const STATUS = {'解析成功':['pass','#3fb950'],'可达':['circle-filled','#3794ff'],'需JS渲染':['symbol-misc','#b180d7'],'反爬拦截':['shield','#d7a33a'],'不可达':['debug-disconnect','#f14c4c'],'域名失效':['error','#f14c4c'],'已关停':['circle-outline','#8b8b8b'],'无网址':['circle-outline','#8b8b8b'],'未检测':['circle-outline','#8b8b8b']};
function AppBase() { return {
  tabs: [], active: null, checked: [], tip: null, pstats: {}, ptree: {regions: [], countries: [], types: []}, seq: 0, sel: null, menuOpen: null, ctx: null, form: null, testRes: null, dlg: null, palette: false, pq: '', pi: 0, quick: '',
  showSide: true, showProps: true, sideW: 230, propsW: 260, drag: null, dragData: null, collapsed: {}, theme: localStorage.getItem('ph-theme') || 'dark',
  busy: 0, task: null, toasts: [], online: true, health: {}, dash: {}, all: [], srcs: [], adapters: {}, providers: {}, facets: {countries: [], types: []}, alertsList: [],
  cols: [{k:'id',label:'ID',w:50,num:1},{k:'title',label:'商品',w:300},{k:'platform',label:'来源平台',w:120},{k:'category',label:'类目',w:90},{k:'chain',label:'产业链',w:160},{k:'brand',label:'品牌',w:90},
         {k:'seller',label:'卖家',w:130},{k:'last_price',label:'现价',w:100,num:1,fmt:(v,p)=>App.money(v,p.currency)},{k:'conf',label:'置信度',w:110,html:p=>App.confBadge(p.last_conf)},{k:'cost',label:'成本',w:90,num:1,fmt:v=>App.money(v)}],
  get menus() { return [
    {name:'文件', items:[{label:'一键监控商品链接…',icon:'link',key:'Ctrl+M',run:()=>this.openQuickMonitor()},{label:'新建数据源…',icon:'add',key:'Ctrl+N',run:()=>this.editSource(null)},{label:'导入 CSV 商品…',icon:'go-to-file',run:()=>this.importCsv()},{label:'导出商品为 CSV',icon:'export',key:'Ctrl+E',run:()=>this.exportCsv()},'-',
      {label:'关闭标签页',icon:'close',key:'Ctrl+W',run:()=>this.closeTab(this.active)},{label:'关闭全部标签页',run:()=>{this.tabs=[];this.active=null}}]},
    {name:'编辑', items:[{label:'撤销上一步',icon:'discard',key:'Ctrl+Z',run:()=>this.undo()},'-',{label:'复制选中项名称',icon:'copy',key:'Ctrl+Shift+C',run:()=>this.copySel()},'-',{label:'查找商品…',icon:'search',key:'Ctrl+F',run:()=>this.openSearch()},{label:'命令面板…',icon:'terminal',key:'Ctrl+K',run:()=>this.openPalette()}]},
    {name:'视图', items:[{label:'切换资源管理器',icon:'layout-sidebar-left',key:'Ctrl+B',run:()=>this.showSide=!this.showSide},{label:'切换属性面板',icon:'layout-sidebar-right',key:'Ctrl+Alt+B',run:()=>this.showProps=!this.showProps},
      {label:'切换深色/浅色主题',icon:'color-mode',key:'Ctrl+Shift+T',run:()=>this.toggleTheme()},'-',...Object.keys(MODS).map(k=>({label:MODS[k][1],icon:MODS[k][0],run:()=>this.openMod(k)})),'-',{label:'后台任务',icon:'tasklist',run:()=>this.showTasks=true},{label:'恢复默认布局',icon:'layout',run:()=>this.resetLayout()},{label:'刷新',icon:'refresh',key:'F5',run:()=>this.reload()}]},
    {name:'数据源', items:[{label:'数据源管理',icon:'plug',run:()=>this.openMod('sources')},{label:'新建数据源…',icon:'add',key:'Ctrl+N',run:()=>this.editSource(null)},{label:'立即抓取全部',icon:'refresh',key:'Ctrl+R',run:()=>this.runMonitor()},'-',{label:'内置平台目录（920 平台）',icon:'globe',run:()=>this.openMod('catalog')},{label:'检查平台连通性',icon:'pulse',run:()=>this.runHealth()}]},
    {name:'工具', items:[{label:'跨平台比价',icon:'diff',run:()=>this.openMod('compare')},{label:'深度对比（已勾选）',icon:'split-horizontal',run:()=>this.openDeep(this.checked)},{label:'定价模型',icon:'tag',run:()=>this.openMod('pricing')},{label:'AI 搜索（Top 10）',icon:'sparkle',run:()=>this.openMod('ai')},{label:'以图搜图',icon:'eye',run:()=>this.openSearch('', 'image')},{label:'价格告警',icon:'bell',run:()=>this.openMod('alerts')},'-',{label:'告警规则与通知渠道',icon:'bell-dot',run:()=>this.openMod('rules')},{label:'同款匹配',icon:'link',run:()=>this.openMod('match')},{label:'控价与卖家画像',icon:'shield',run:()=>this.openMod('map')},{label:'报表与促销日历',icon:'graph',run:()=>this.openMod('reports')},{label:'设置与集成',icon:'settings-gear',run:()=>this.openMod('admin')}]},
    {name:'帮助', items:[{label:'新手引导',icon:'rocket',run:()=>this.openOnboarding()},{label:'设置 AI 服务…',icon:'key',run:()=>this.aiSettings()},'-',{label:'键盘快捷键',icon:'list-flat',run:()=>this.showKeys()},{label:'置信度说明',icon:'law',run:()=>this.confHelp()},{label:'开发者接口文档',icon:'link-external',run:()=>window.open('/docs')},{label:'关于 PriceHub',icon:'info',run:()=>this.about()}]}]; },
  get toolbar() { return [['dashboard','仪表盘',()=>this.openMod('dashboard')],['package','商品监控',()=>this.openMod('products')],['search','搜索 (Ctrl+F)',()=>this.openSearch()],['sparkle','AI 搜索',()=>this.openMod('ai')],'|',
    ['link','一键监控商品链接 (Ctrl+M)',()=>this.openQuickMonitor()],['add','新建数据源 (Ctrl+N)',()=>this.editSource(null)],['refresh','立即抓取全部 (Ctrl+R)',()=>this.runMonitor()],['export','导出 CSV',()=>this.exportCsv()],'|',
    ['diff','比价',()=>this.openMod('compare')],['split-horizontal','深度对比',()=>this.openDeep(this.checked)],['tag','定价',()=>this.openMod('pricing')],['globe','平台目录',()=>this.openMod('catalog')],['bell','告警',()=>this.openMod('alerts')],['bell-dot','告警规则与通知',()=>this.openMod('rules')],['shield','控价与卖家',()=>this.openMod('map')],['graph','报表与日历',()=>this.openMod('reports')],['settings-gear','设置与集成',()=>this.openMod('admin')],'|',
    ['layout-sidebar-left','切换侧栏 (Ctrl+B)',()=>this.showSide=!this.showSide],['layout-sidebar-right','切换属性面板',()=>this.showProps=!this.showProps],['color-mode','切换主题',()=>this.toggleTheme()],['terminal','命令面板 (Ctrl+K)',()=>this.openPalette()]]; },
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
    setInterval(() => { this.api('/api/dashboard').then(d => this.dash = d).catch(() => {}); this.loadPstats(); }, 15000);
  },
  async refresh() { try { [this.dash, this.all, this.srcs, this.pstats, this.ptree] = await Promise.all([this.api('/api/dashboard'), this.api('/api/products'), this.api('/api/sources'), this.api('/api/platforms/stats'), this.api('/api/platforms/tree')]); } catch (e) { this.toast(e.message, true); } },
  async loadPstats() { try { this.pstats = await this.api('/api/platforms/stats'); } catch (e) {} },
  async reload() { await this.refresh(); const t = this.activeTab(); if (t) this.loadTab(t); this.toast('已刷新'); },
  // ---------- 标签页 ----------
  activeTab() { return this.tabs.find(t => t.id === this.active); },
  newTab(type, extra = {}, focus = true) { const t = {id: 't' + (++this.seq), type, icon: (MODS[type] || ['file'])[0], title: (MODS[type] || ['', ''])[1], ...extra};
    this.tabs.push(t); const tt = this.tabs[this.tabs.length - 1]; if (focus) this.active = t.id; this.loadTab(tt); return tt; },
  openMod(type, focus = true) { const ex = this.tabs.find(t => t.type === type); if (ex) { if (focus) this.active = ex.id; this.loadTab(ex); return ex; }
    const defaults = {products: {f: {q: '', platform: '', category: ''}, sk: 'id', asc: true, pg: 1, per: 20}, search: {m: 'semantic', q: '', res: null, engine: '', err: ''},
      ai: {p: 'deepseek', q: '', r: null}, pricing: {pid: this.all[0] && this.all[0].id, cost: '', markup: 0.2, r: null}, catalog: {country: '', ptype: '', q: '', region: '', status: '', strategy: '', off: 0, cat: {}}, sources: {hidePf: true}};
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
    if (t.type === 'deep' && !t.d) await this.loadDeep(t);
  } catch (e) { this.toast(e.message, true); } },
  openProduct(id) { const ex = this.tabs.find(t => t.type === 'product' && t.pid == id); if (ex) { this.active = ex.id; return; }
    const p = this.all.find(x => x.id == id); this.newTab('product', {pid: id, icon: 'file', title: p ? p.title : '商品 #' + id, d: null, np: '', cost: '', markup: 0.2}); },
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
  redrawCharts() { this.tabs.forEach(t => { if (t.type === 'dashboard') this.dashCharts(t); if (t.type === 'product' && t.d && t.d.p) this.priceChart(t); if (t.type === 'deep' && t.d && t.d.items) this.deepChart(t); }); },
  statRows(t) { const s = t.d.h.stats, f = t.d.f; return [['记录数', s.count], ['最低价', this.money(s.min)], ['最高价', this.money(s.max)], ['均价', this.money(s.avg)], ['涨跌幅', (s.change_pct ?? 0) + '%'],
    ['移动平均', this.money(f.moving_average)], ['指数平滑', this.money(f.exp_smoothing)], ['日斜率', f.slope ?? '—'], ['7期后预测', this.money((f.linear_regression || []).slice(-1)[0]), f.conf]]; },
  // ---------- 商品表 ----------
  uniq(k) { return [...new Set(this.all.map(p => p[k]).filter(Boolean))].sort(); },
  filtered(t) { const f = t.f, q = f.q.toLowerCase();
    return this.all.filter(p => (!q || (p.title + ' ' + (p.brand || '')).toLowerCase().includes(q)) && (!f.platform || p.platform === f.platform) && (!f.category || p.category === f.category) && (!f.chain || p.chain === f.chain) && (!f.fav || (this.favs||[]).includes(p.id)))
      .sort((a, b) => { const g = p => t.sk === 'conf' ? (p.last_conf || {}).confidence : p[t.sk]; const x = g(a) ?? '', y = g(b) ?? ''; return (x > y ? 1 : x < y ? -1 : 0) * (t.asc ? 1 : -1); }); },
  pages(t) { return Math.max(1, Math.ceil(this.filtered(t).length / t.per)); },
  paged(t) { if (t.pg > this.pages(t)) t.pg = this.pages(t); return this.filtered(t).slice((t.pg - 1) * t.per, t.pg * t.per); },
  sortBy(t, k) { t.asc = t.sk === k ? !t.asc : true; t.sk = k; },
  // ---------- 选择/属性/右键 ----------
  select(kind, obj) { this.sel = {kind, obj}; },
  propRows() { const o = this.sel.obj; const M = {product: [['ID','id'],['标题','title'],['平台','platform'],['平台商品号','external_id'],['类目','category'],['产业链','chain'],['品牌','brand'],['现价','last_price'],['成本','cost'],['币种','currency'],['同款分组','group_key'],['链接','url']],
      source: [['ID','id'],['名称','name'],['接入方式','type'],['启用','enabled'],['配置','config']], platform: [['平台','name'],['网址','url'],['大洲','region'],['国家','country'],['类型','type'],['范围','scope'],['说明','note']]}[this.sel.kind];
    const rows = M.map(([l, k]) => [l, k === 'last_price' ? ESC(this.money(o[k])) + ' ' + App.confBadge(o.last_conf) : k === 'status' ? this.statusBadge(o[k]) : ESC(typeof o[k] === 'object' && o[k] ? JSON.stringify(o[k]) : (o[k] ?? '—'))]);
    if (this.sel.kind === 'platform') rows.push(['状态', this.statusBadge(o.status)], ['检测详情', ESC(o.status_detail || '未检查')], ['读取方式', ESC(this.stratLabel(o.strategy))], ['语言', ESC(o.lang)], ['币种', ESC(o.currency)]);
    if (this.sel.kind === 'product') rows.push(['卖家', ESC(o.seller || '—')], ['属性', ESC(Object.entries(o.attrs || {}).map(([k, v]) => k + ':' + v).join('；') || '—')]);
    return rows; },
  showCtx(e, p) { this.ctx = {x: Math.min(e.clientX, innerWidth - 200), y: Math.min(e.clientY, innerHeight - 200), items: [
    {label: '在新标签页打开详情', icon: 'go-to-file', run: () => this.openProduct(p.id)}, {label: '与同款商品深度对比', icon: 'split-horizontal', run: () => this.deepSameGroup(p)}, {label: '加入深度对比（对比篮）', icon: 'add', run: () => this.toggleCheck(p.id, true)}, '-', {label: '定价建议…', icon: 'tag', run: () => { const t = this.openMod('pricing'); t.pid = p.id; this.pricingTab(t); }},
    {label: '查找相似商品（语义）', icon: 'search', run: () => this.openSearch(p.title, 'semantic', true)}, '-', {label: '复制名称', icon: 'copy', run: () => this.copy(p.title)}, {label: '复制价格', icon: 'copy', run: () => this.copy(String(p.last_price))},
    '-', {label: '记录当前价格…', icon: 'edit', run: () => this.prompt('记录价格', '为「' + p.title + '」录入新价格：', p.last_price, async v => { await this.post(`/api/products/${p.id}/prices`, {price: Number(v)}); this.toast('已记录'); this.refresh(); })}]}; },
  showSrcCtx(e, s) { this.ctx = {x: e.clientX, y: e.clientY, items: [{label: '立即抓取', icon: 'play', run: () => this.fetchSource(s)}, {label: '编辑…', icon: 'edit', run: () => this.editSource(s)}, {label: '复制配置 JSON', icon: 'copy', run: () => this.copy(s.config)}, '-', {label: '删除', icon: 'trash', run: () => this.delSource(s)}]}; },
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
      {key: 'plat', label: '商品·按平台', children: Object.keys(pc).sort().map(k => ({label: k, icon: 'symbol-misc', count: pc[k], run: () => this.filterProducts('platform', k)}))},
      {key: 'cat', label: '商品·按类目', children: Object.keys(cc).sort().map(k => ({label: k, icon: 'list-flat', count: cc[k], run: () => this.filterProducts('category', k)}))},
      {key: 'preg', label: '内置平台·按大洲（' + (this.pstats.total || 0) + '）', children: (this.ptree.regions || []).map(r => ({label: r.k, icon: 'globe', count: (r.e ? r.e + '/' : '') + r.n, run: () => this.openCatalog({region: r.k})}))},
      {key: 'pcty', label: '内置平台·按国家', children: (this.ptree.countries || []).filter(c => c.n >= 3).map(c => ({label: c.k, icon: 'location', count: (c.e ? c.e + '/' : '') + c.n, run: () => this.openCatalog({country: c.k})}))},
      {key: 'ptype', label: '内置平台·按类型', children: (this.ptree.types || []).slice(0, 15).map(c => ({label: c.k, icon: 'tag', count: c.n, run: () => this.openCatalog({ptype: c.k})}))},
      {key: 'pst', label: '内置平台·按体检状态', children: (this.pstats.by_status || []).map(s => ({label: s.k, icon: (STATUS[s.k] || ['circle-outline'])[0], color: (STATUS[s.k] || [])[1], count: s.n, run: () => this.openCatalog({status: s.k})}))}]; },
  isActiveNode(n) { const t = this.activeTab(); return n.mod && t && t.type === n.mod; },
  filterProducts(k, v) { const t = this.openMod('products'); t.f = {q: '', platform: '', category: '', [k]: v}; t.pg = 1; },
  // ---------- 快捷键/命令 ----------
  keysBase(e) { const c = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (e.key === 'Escape') { this.closePalette(); this.ctx = null; this.menuOpen = null; if (!this.form && this.dlg) this.dlg = null; else this.form = null; return; }
    if (e.key === 'Escape') this.tip = null;
    const map = {d: () => this.openDeep(this.checked), k: () => this.openPalette(), f: () => this.openSearch(), n: () => this.editSource(null), w: () => this.closeTab(this.active), b: () => e.altKey ? this.showProps = !this.showProps : this.showSide = !this.showSide, r: () => this.runMonitor(), e: () => this.exportCsv()};
    if (c && e.shiftKey && k === 't') { e.preventDefault(); return this.toggleTheme(); }
    if (c && e.shiftKey && k === 'c') { e.preventDefault(); return this.copySel(); }
    if (c && !e.shiftKey && map[k]) { e.preventDefault(); map[k](); }
    if (e.key === 'F5') { e.preventDefault(); this.reload(); }
    if (c && e.key === 'Tab' && this.tabs.length) { e.preventDefault(); const i = this.tabs.findIndex(t => t.id === this.active); this.active = this.tabs[(i + (e.shiftKey ? -1 : 1) + this.tabs.length) % this.tabs.length].id; } },
  openPalette() { this.palette = true; this.pq = ''; this.pi = 0; this.$nextTick(() => this.$refs.pin.focus()); },
  // 关闭前先显式移除输入框焦点：x-show 隐藏后 Chrome 要到下一帧才把焦点从隐藏元素移走，
  // 期间若立刻重开面板，activeElement 仍是旧输入框（看似已聚焦），随后焦点被浏览器回收到 body，输入的文字就丢了。
  closePalette() { const i = this.$refs.pin; if (i && document.activeElement === i) i.blur(); this.palette = false; },
  pcmds() { const q = this.pq.trim().toLowerCase(); const cmds = [];
    this.menus.forEach(m => m.items.forEach(it => it !== '-' && cmds.push({label: m.name + ': ' + it.label, icon: it.icon, key: it.key, run: it.run})));
    this.all.forEach(p => cmds.push({label: '打开商品: ' + p.title, icon: 'file', cat: p.platform, run: () => this.openProduct(p.id)}));
    cmds.push({label: '工具: 深度对比已勾选（' + this.checked.length + '）', icon: 'split-horizontal', run: () => this.openDeep(this.checked)});
    return cmds.filter(c => !q || c.label.toLowerCase().includes(q)).slice(0, 30); },
  runCmd(c) { if (!c) return; this.closePalette(); c.run(); },
  toggleTheme() { this.theme = this.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = this.theme; localStorage.setItem('ph-theme', this.theme); this.$nextTick(() => this.redrawCharts()); },
  showKeys() { this.dlg = {title: '键盘快捷键', lines: [['Ctrl+K','命令面板'],['Ctrl+M','一键监控商品链接（也可直接 Ctrl+V 粘贴链接）'],['Ctrl+Z','撤销删除/批量操作'],['Ctrl+F','查找商品'],['Ctrl+N','新建数据源'],['Ctrl+R','立即抓取全部'],['Ctrl+E','导出 CSV'],['Ctrl+W','关闭标签页'],['Ctrl+Tab','切换标签页'],['Ctrl+B','切换资源管理器'],['Ctrl+Alt+B','切换属性面板'],['Ctrl+Shift+T','切换主题'],['Ctrl+Shift+C','复制选中项'],['Ctrl+D','深度对比已勾选'],['F5','刷新'],['Esc','关闭对话框/菜单']].map(r => `<b style="display:inline-block;width:130px">${r[0]}</b>${r[1]}`)}; },
  about() { this.dlg = {title: '关于 PriceHub', lines: ['<b>PriceHub</b> 全球交易平台商品价格全生命周期管理', '文本引擎：' + this.health.text_engine, '图像引擎：' + this.health.image_engine, '接入方式：' + (this.health.adapters || []).map(a => TYPE_LABEL[a] || a).join('、'), '平台目录：' + this.health.platform_catalog + ' 个']}; },
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
  async runMonitor() { const list = (await this.api('/api/sources')).filter(s => s.enabled && !(s.type === 'platform' && (c => !(c.urls || []).length && !c.query)(JSON.parse(s.config)))); let ok = 0;
    for (let i = 0; i < list.length; i++) { this.task = {label: `抓取 ${i + 1}/${list.length}：${list[i].name}`, p: i / list.length};
      try { await this.post(`/api/sources/${list[i].id}/fetch`, {}); ok++; } catch (e) { this.toast(list[i].name + '：' + e.message, true); } }
    this.task = null; this.toast(`监控抓取完成：${ok}/${list.length} 个数据源成功`); await this.refresh(); const t = this.activeTab(); if (t) this.loadTab(t); },
  async loadCatalog(t) { const P = new URLSearchParams({limit: 100, offset: t.off || 0, country: t.country, type: t.ptype, q: t.q, region: t.region || '', status: t.status || '', strategy: t.strategy || ''});
    try { t.cat = await this.api('/api/platforms?' + P); } catch (e) { this.toast(e.message, true); } },
  openCatalog(f) { const t = this.openMod('catalog'); Object.assign(t, {country: '', ptype: '', q: '', region: '', status: '', strategy: '', off: 0}, f); this.loadCatalog(t); },
  async togglePlatform(p) { try { const r = await this.post(`/api/platforms/${p.id}/${p.enabled ? 'disable' : 'enable'}`, {}); p.enabled = p.enabled ? 0 : 1; if (r.source_id) p.source_id = r.source_id;
      this.toast((p.enabled ? '已启用：' : '已停用：') + p.name); this.loadPstats(); this.ptree = await this.api('/api/platforms/tree'); this.srcs = await this.api('/api/sources'); } catch (e) { this.toast(e.message, true); } },
  async bulkEnable(t) { try { const r = await this.post('/api/platforms/bulk_enable', {country: t.country}); this.toast(`已启用「${t.country}」${r.enabled} 个平台`); await this.loadCatalog(t); this.loadPstats(); this.ptree = await this.api('/api/platforms/tree'); } catch (e) { this.toast(e.message, true); } },
  async runHealth() { const r = await this.post('/api/platforms/healthcheck', {}); this.toast(r.message, !r.ok); if (!r.ok) return;
    this.task = {label: '平台连通性体检中…', p: .1}; const iv = setInterval(async () => { await this.loadPstats(); this.task = this.pstats.running ? {label: '平台连通性体检中…（已体检 ' + this.pstats.checked + '）', p: Math.min(.95, this.pstats.checked / this.pstats.total)} : null;
      if (!this.pstats.running) { clearInterval(iv); this.toast('体检完成'); const t = this.tabs.find(x => x.type === 'catalog'); if (t) this.loadCatalog(t); } }, 3000); },
  // ---------- 置信度 ----------
  confBadge(c) { return App.confBadge(c); },
  statusBadge(s) { const [ic, col] = STATUS[s] || ['circle-outline', '#8b8b8b']; return `<span class="st" style="color:${col}"><i class="codicon codicon-${ic}"></i>${ESC(s)}</span>`; },
  methodName(m) { return METHOD_NAME[m] || m || '—'; },
  tipOver(e) { const el = e.target.closest && e.target.closest('[data-conf]'); if (!el) { if (this.tip) this.tip = null; return; }
    const d = JSON.parse(decodeURIComponent(el.dataset.tip)); const r = el.getBoundingClientRect();
    this.tip = {title: d.title, basis: d.basis, x: Math.min(r.left, innerWidth - 380), y: r.bottom + 6 > innerHeight - 160 ? r.top - 150 : r.bottom + 6}; },
  confHelp() { this.dlg = {title: '价格置信度说明', lines: ['每个价格都带 0-1 的置信度与等级：<b class="lv-高">高</b> 不低于 0.75，<b class="lv-中">中</b> 不低于 0.5，<b class="lv-低">低</b> 低于 0.5。',
    '按来源打基础分：数据接口 0.95 &gt; 表格导入 0.90 &gt; 网页标准商品数据 0.85 &gt; 网页价格标注 0.80 &gt; 页面位置规则 0.75 &gt; 网页分享信息 0.60 &gt; 手动录入 0.50 &gt; 网页文字识别 0.45 &gt; AI 搜索 0.40',
    '再按情况打折：比历史价或同款价偏离超过 15% 打 85 折、超过 30% 打 6 折；缺币种或商品名打 9 折；数据越旧越不可信（1 天内不打折，7 天内 9 折，30 天内 75 折，更久 6 折）。',
    '价格预测的可信度看两点：历史走势是否平稳、历史记录是否足够多。定价建议的可信度看是否填写了成本、有几个竞品价格可参考。把鼠标移到徽标上可看到具体原因。']}; },
  // ---------- 深度对比 ----------
  toggleCheck(id, add) { const i = this.checked.indexOf(id); if (i >= 0 && !add) this.checked.splice(i, 1); else if (i < 0) { this.checked.push(id); if (add) this.toast('已加入对比篮（' + this.checked.length + '）'); } },
  checkAll(t, on) { const ids = this.paged(t).map(p => p.id); this.checked = on ? [...new Set([...this.checked, ...ids])] : this.checked.filter(i => !ids.includes(i)); },
  deepSameGroup(p) { const ids = this.all.filter(x => x.group_key === p.group_key).map(x => x.id); if (ids.length < 2) return this.toast('未找到其他平台的同款商品', true); this.openDeep(ids); },
  openDeep(ids, tab) { ids = [...new Set((ids || []).map(Number))]; if (ids.length < 2) return this.toast('请至少选择 2 个商品进行深度对比', true);
    if (tab) { tab.ids = ids; tab.d = null; tab.title = '深度对比（' + ids.length + '）'; this.active = tab.id; return this.loadDeep(tab); }
    this.newTab('deep', {ids, icon: 'split-horizontal', title: '深度对比（' + ids.length + '）', d: null, onlyDiff: false}); },
  async loadDeep(t) { try { t.d = await this.api('/api/compare/deep?ids=' + t.ids.join(',')); this.$nextTick(() => this.deepChart(t)); } catch (e) { t.d = {error: e.message}; } },
  deepRows(t) { const D = t.d || {}; if (!D.items) return []; const I = D.items, M = v => ESC(this.money(v)), R = [], rk = D.rank || {};
    const num = (label, key, get, fmt, extra) => { const diff = new Set(I.map(it => String(get(it)))).size > 1;
      R.push({label, num: 1, vals: I.map(it => fmt(get(it)) + (extra ? ' ' + extra(it) : '')), diff, best: diff ? (rk[key] || {}).best : null, worst: diff ? (rk[key] || {}).worst : null}); };
    num('当前价', 'last_price', it => it.product.last_price, M, it => App.confBadge(it.product.last_conf));
    num('置信度', 'confidence', it => (it.product.last_conf || {}).confidence, v => v == null ? '—' : v.toFixed(2));
    num('历史最低', 'min', it => it.stats.min, M); num('历史最高', 'max', it => it.stats.max, M); num('均价', 'avg', it => it.stats.avg, M);
    num('涨跌幅', 'change_pct', it => it.stats.change_pct, v => v == null ? '—' : v + '%');
    R.push({label: '记录数', num: 1, vals: I.map(it => ESC(it.stats.count)), diff: new Set(I.map(it => it.stats.count)).size > 1});
    R.push({label: '趋势', vals: I.map(it => ESC(it.forecast.trend || '数据不足')), diff: new Set(I.map(it => it.forecast.trend)).size > 1});
    num('7期后预测', 'forecast_7', it => (it.forecast.linear_regression || [null]).slice(-1)[0], M, it => App.confBadge(it.forecast.conf));
    R.push({label: '最近更新', vals: I.map(it => ESC(this.fmtTs(it.product.last_ts))), diff: false});
    Object.entries(D.fields).forEach(([k, l]) => R.push({label: l, vals: I.map(it => ESC(it.product[k] || '—')), diff: D.diff[k]}));
    D.attr_keys.forEach(k => R.push({label: '属性·' + k, vals: I.map(it => ESC((it.product.attrs || {})[k] || '—')), diff: D.attr_diff[k]}));
    R.push({label: '详情', vals: I.map(it => `<a href="#" onclick="return false" data-open-pid="${it.product.id}">进入详情</a>`), diff: false});
    return R; },
  deepChart(t) { const I = t.d.items, ts = [...new Set(I.flatMap(it => it.history.map(h => h.ts)))].sort();
    drawChart('dc-' + t.id, {type: 'line', data: {labels: ts.map(x => this.fmtTs(x).slice(0, 11)), datasets: I.map((it, j) => { const m = Object.fromEntries(it.history.map(h => [h.ts, h.price]));
      return {label: it.product.platform + ' · ' + it.product.title.slice(0, 18), data: ts.map(x => m[x] ?? null), spanGaps: true, borderColor: PALETTE[j % PALETTE.length], backgroundColor: PALETTE[j % PALETTE.length], tension: .25, pointRadius: 2}; })},
      options: this.chartOpts({interaction: {mode: 'index', intersect: false}})}); },
  openPid(e) { const a = e.target.closest && e.target.closest('[data-open-pid]'); if (a) { e.preventDefault(); this.openProduct(+a.dataset.openPid); } },
  cell(c, p) { if (c.html) return c.html(p); return ESC(c.fmt ? c.fmt(p[c.k], p) : (p[c.k] ?? '—')); },
}; }
App.confBadge = c => { if (!c || c.confidence == null) return '<span class="muted">—</span>';
  const tip = encodeURIComponent(JSON.stringify({title: '置信度 ' + c.confidence.toFixed(2) + '（' + c.level + '）', basis: c.basis || []}));
  return `<span class="conf lv-${c.level}" data-conf data-tip="${tip}"><i class="codicon codicon-circle-filled"></i>${c.level} ${c.confidence.toFixed(2)}</span>`; };
App.money = (v, c) => v == null || v === '' || isNaN(v) ? '—' : (c && c !== 'CNY' ? c + ' ' : '¥') + Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
