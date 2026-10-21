// v4 体验优化：首次引导、全局搜索建议、一键监控、后台任务中心、批量操作与撤销、布局记忆、虚拟滚动、骨架屏、平台不可用说明
const ST_LABEL = {'解析成功':'可读取价格','可达':'可访问','需JS渲染':'需浏览器渲染','反爬拦截':'拦截自动访问','不可达':'无法连接','域名失效':'网址失效','已关停':'已关停','无网址':'缺少网址','未检测':'未检查'};
const TYPE_LABEL = {mock:'演示数据',webpage:'网页',platform:'内置平台',rest:'数据接口 (API)',csv:'表格文件 (CSV)'};
const LS_KEY = 'ph-layout-v4';
const MEMO = {};   // 非响应式缓存：避免在渲染中写响应式数据
const V4 = {
  settings: {onboarded: true}, ob: {step: 0, countries: [], provider: 'deepseek', key: '', url: '', task: null, demo: false, saving: false}, obOpen: false, sug: null, si: 0, qm: {url: '', category: '', task: null}, qmOpen: false, tasksList: [], showTasks: false, taskPoll: null, lastUndo: null, layoutSaved: '', obCountries: [], xp: null, loaded: false, pAll: null, pVer: 0, allVer: 0,
  get runningTasks() { return this.tasksList.filter(t => t.status === '排队中' || t.status === '进行中'); },
  // ---------- 启动 ----------
  async init() {
    document.documentElement.dataset.theme = this.theme; this.loadLayout();
    try { [this.health, this.adapters, this.providers, this.facets, this.settings] = await Promise.all(['/api/health','/api/adapters','/api/ai/providers','/api/platforms/facets','/api/settings'].map(u => this.api(u))); }
    catch (e) { this.toast('初始化失败：' + e.message, true); }
    await this.refresh(); this.restoreTabs();
    if (!this.settings.onboarded) this.openOnboarding();
    setInterval(() => { this.api('/api/dashboard').then(d => this.dash = d).catch(() => {}); this.loadPstats(); }, 15000);
    setInterval(() => this.saveLayout(), 800);
    window.addEventListener('paste', e => this.onPaste(e));
    this.pollTasks();
  },
  // ---------- 布局/筛选/列宽记忆 ----------
  loadLayout() { try { const L = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); if (!L) { this.collapsed = {pcty: true, ptype: true, pst: true}; return; }
    ['showSide','showProps','sideW','propsW','collapsed'].forEach(k => L[k] != null && (this[k] = L[k]));
    (L.colW && this.cols.forEach(c => L.colW[c.k] && (c.w = L.colW[c.k]))); this._saved = L; } catch (e) {} },
  layoutObj() { const mods = this.tabs.filter(t => MODS[t.type]);
    return {showSide: this.showSide, showProps: this.showProps, sideW: this.sideW, propsW: this.propsW, collapsed: this.collapsed, colW: Object.fromEntries(this.cols.map(c => [c.k, c.w])),
      tabs: mods.map(t => t.type), active: (this.activeTab() || {}).type, filters: Object.fromEntries(mods.filter(t => t.type === 'products' || t.type === 'catalog').map(t => [t.type,
        t.type === 'products' ? {f: t.f, sk: t.sk, asc: t.asc} : {country: t.country, ptype: t.ptype, q: t.q, region: t.region, status: t.status, strategy: t.strategy}]))}; },
  saveLayout() { if (!this._ready) return; const s = JSON.stringify(this.layoutObj()); if (s !== this.layoutSaved) { this.layoutSaved = s; localStorage.setItem(LS_KEY, s); } },
  restoreTabs() { const L = this._saved; const list = (L && L.tabs && L.tabs.length) ? L.tabs : ['dashboard', 'products'];
    list.forEach(type => { const t = this.openMod(type, false); const f = L && L.filters && L.filters[type]; if (f) { if (type === 'products') Object.assign(t, JSON.parse(JSON.stringify(f))); else Object.assign(t, f); } });
    const act = this.tabs.find(t => t.type === ((L && L.active) || 'dashboard')) || this.tabs[0]; if (act) this.active = act.id; this._ready = true; },
  resetLayout() { localStorage.removeItem(LS_KEY); this.toast('已恢复默认布局，刷新后生效'); },
  // ---------- 提示（支持操作按钮，如“撤销”） ----------
  toast(msg, err = false, action = null, ms) { const id = Math.random(); this.toasts.push({id, msg, err, action}); setTimeout(() => this.toasts = this.toasts.filter(t => t.id !== id), ms || (action ? 8000 : 4000)); },
  toastAct(t) { this.toasts = this.toasts.filter(x => x.id !== t.id); t.action.run(); },
  // ---------- 首次引导 ----------
  async openOnboarding() { this.ob = {step: 0, countries: [...(this.settings.countries || [])], provider: this.settings.ai_provider || 'deepseek', key: '', url: '', task: null, demo: false, saving: false}; this.obOpen = true;
    try { this.obCountries = await this.api('/api/onboarding/countries'); } catch (e) {} },
  obToggle(c) { const i = this.ob.countries.indexOf(c); i >= 0 ? this.ob.countries.splice(i, 1) : this.ob.countries.push(c); },
  async obNext() { const o = this.ob;
    if (o.step === 1 && o.key.trim()) { try { await this.post('/api/settings', {ai_provider: o.provider, ai_key: o.key}); this.providers = await this.api('/api/ai/providers'); this.toast('已保存 AI 密钥'); } catch (e) { return this.toast(e.message, true); } }
    if (o.step < 3) o.step++; else this.obFinish(); },
  async obMonitor() { const o = this.ob; if (!o.url.trim()) return this.toast('请先粘贴一个商品链接', true);
    try { o.task = await this.post('/api/quick-monitor', {url: o.url.trim()}); this.trackTask(o.task.id, t => { o.task = t; }); } catch (e) { this.toast(e.message, true); } },
  async obDemo() { const o = this.ob; o.demo = 'loading';
    try { for (const [n, cfg] of [['演示：淘宝', {platform: 'taobao.com', seed: 1}], ['演示：京东', {platform: 'jd.com', bias: 1.06, seed: 2}]]) {
        const r = await this.post('/api/sources', {name: n, type: 'mock', config: cfg}); await this.post(`/api/sources/${r.id}/fetch`, {}); }
      o.demo = true; await this.refresh(); this.toast('已加入演示数据'); } catch (e) { o.demo = false; this.toast(e.message, true); } },
  async obFinish(skip) { const o = this.ob; o.saving = true;
    try { if (!skip) for (const c of o.countries) await this.post('/api/platforms/bulk_enable', {country: c});
      this.settings = await this.post('/api/settings', {onboarded: true, countries: skip ? [] : o.countries}); } catch (e) { this.toast(e.message, true); }
    this.obOpen = false; o.saving = false; await this.refresh(); this.loadPstats(); if (!skip && o.countries.length) this.toast('已启用 ' + o.countries.join('、') + ' 的平台'); },
  // ---------- 全局搜索建议 ----------
  async suggest() { const q = this.quick.trim(); if (!q) { this.sug = null; return; }
    try { const r = await this.api('/api/suggest?q=' + encodeURIComponent(q)); if (this.quick.trim() !== q) return; this.sug = r; this.si = 0; } catch (e) {} },
  sugItems() { const s = this.sug; if (!s) return []; const out = [];
    if (s.url) out.push({sec: '链接', icon: 'link', label: '一键监控这个商品链接', right: s.url.platform ? s.url.platform.name : '未收录网站', run: () => this.openQuickMonitor(s.url.url, true), attr: 'monitor'});
    s.products.forEach(p => out.push({sec: '商品', icon: 'package', label: p.title, right: this.money(p.last_price, p.currency), run: () => this.openProduct(p.id), attr: 'product'}));
    s.categories.forEach(c => out.push({sec: '类目', icon: 'list-flat', label: c, right: '筛选商品', run: () => this.filterProducts('category', c), attr: 'category'}));
    s.platforms.forEach(p => out.push({sec: '平台', icon: 'globe', label: p.name, right: p.country + ' · ' + (ST_LABEL[p.status] || '未检查'), run: () => this.openCatalog({q: p.name}), attr: 'platform'}));
    if (!s.url) { out.push({sec: '搜索', icon: 'search', label: '语义搜索「' + s.q + '」（支持任意语言）', right: 'Enter', run: () => this.openSearch(s.q, 'semantic', true), attr: 'semantic'});
      out.push({sec: '搜索', icon: 'sparkle', label: 'AI 全网搜索「' + s.q + '」', right: '', run: () => { const t = this.openMod('ai'); t.q = s.q; this.aiSearch(t); }, attr: 'ai'}); }
    out.forEach((x, i) => x.head = i === 0 || out[i - 1].sec !== x.sec); return out; },
  sugKey(e) { const it = this.sugItems();
    if (e.key === 'ArrowDown') { e.preventDefault(); this.si = Math.min(this.si + 1, it.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.si = Math.max(this.si - 1, 0); }
    else if (e.key === 'Enter') { e.preventDefault(); const qq = this.quick.trim(); if (/^https?:\/\//i.test(qq)) return this.sugRun({run: () => this.openQuickMonitor(qq, true)});
      const x = it[this.si]; if (x && this.sug && this.sug.q === this.quick.trim()) this.sugRun(x); else if (qq) this.sugRun({run: () => this.openSearch(qq, 'semantic', true)}); }
    else if (e.key === 'Escape') { this.sug = null; e.target.blur(); } },
  sugRun(x) { this.sug = null; this.quick = ''; x.run(); },
  // ---------- 一键监控 ----------
  onPaste(e) { const el = document.activeElement; if (el && /INPUT|TEXTAREA|SELECT/.test(el.tagName)) return;
    const txt = (e.clipboardData && e.clipboardData.getData('text') || '').trim(); if (/^https?:\/\/\S+$/i.test(txt)) { e.preventDefault(); this.openQuickMonitor(txt, true); } },
  qr() { return (this.qm && this.qm.task && this.qm.task.result) || {}; }, qa() { return (this.qm && this.qm.task && this.qm.task.advice) || {}; }, obr() { return (this.ob && this.ob.task && this.ob.task.result) || {}; },
  openQuickMonitor(url = '', go = false) { this.qm = {url, category: '', task: null}; this.qmOpen = true; this.$nextTick(() => { const i = document.querySelector('[data-qm-url]'); i && i.focus(); }); if (go && url) this.qmGo(); },
  async qmGo() { const m = this.qm; if (!this.qmOpen || !m.url.trim()) return this.toast('请粘贴商品链接', true);
    try { m.task = await this.post('/api/quick-monitor', {url: m.url.trim(), category: m.category}); this.trackTask(m.task.id, t => { if (this.qm === m) m.task = t; }); }
    catch (e) { m.task = {status: '失败', error: e.message, advice: null}; } },
  // ---------- 后台任务 ----------
  async newTask(body) { try { const t = await this.post('/api/tasks', body); this.tasksList = [t, ...this.tasksList.filter(x => x.id !== t.id)]; this.trackTask(t.id); return t; } catch (e) { this.toast(e.message, true); } },
  trackTask(id, cb) { (this._watch = this._watch || {})[id] = cb || null; this.pollTasks(true); },
  async pollTasks(force) { if (this.taskPoll && !force) return; clearTimeout(this.taskPoll);
    const tick = async () => { try { const L = await this.api('/api/tasks'); const before = Object.fromEntries(this.tasksList.map(t => [t.id, t.status])); this.tasksList = L;
        for (const t of L) { const w = this._watch && (t.id in this._watch) ? this._watch[t.id] : undefined; if (w) w(t);
          if ((t.status === '已完成' || t.status === '失败') && before[t.id] !== t.status && this._watch && t.id in this._watch) { delete this._watch[t.id]; this.taskDone(t); } } } catch (e) {}
      this.taskPoll = (this.runningTasks.length || Object.keys(this._watch || {}).length) ? setTimeout(tick, 600) : null; };
    tick(); },
  async taskDone(t) { const r = t.result || {};
    if (t.status === '失败') this.toast(t.title + '失败：' + (t.advice ? t.advice.title : t.error), true, t.advice ? {label: '查看原因', run: () => { this.showTasks = true; }} : null);
    else if (t.kind === 'fetch_source') this.toast(`${t.title.replace('抓取', '')}抓取完成：${r.fetched} 条`);
    else if (t.kind === 'monitor_all') this.toast(`监控抓取完成：${r.ok}/${r.sources} 个数据源成功，共 ${r.fetched} 条` + (r.failed ? `，${r.failed} 个失败` : ''), !!r.failed && !r.ok);
    else if (t.kind === 'healthcheck') { this.toast('平台连通性检查完成：' + r.domains + ' 个网站'); const c = this.tabs.find(x => x.type === 'catalog'); if (c) this.loadCatalog(c, true); }
    else if (t.kind === 'quick_monitor') this.toast(`已开始监控：${r.title}（${this.money(r.price, r.currency)}）`, false, {label: '查看商品', run: () => this.openProduct(r.product_id)});
    await this.refresh(); this.loadPstats(); const a = this.activeTab(); if (a && a.type !== 'catalog') this.loadTab(a); },
  async clearTasks() { await this.api('/api/tasks', {method: 'DELETE'}); this.tasksList = await this.api('/api/tasks'); },
  taskCls(t) { return t.status === '已完成' ? 'done' : t.status === '失败' ? 'fail' : (t.progress ? '' : 'ind'); },
  fetchSource(s) { this.newTask({kind: 'fetch_source', source_id: s.id}); this.toast('已在后台抓取「' + s.name + '」，可继续其他操作'); },
  runMonitor() { this.newTask({kind: 'monitor_all'}); this.toast('已在后台抓取全部数据源，进度见左下角「后台任务」'); },
  async runHealth() { const t = await this.newTask({kind: 'healthcheck'}); if (t) this.toast('连通性检查已在后台开始，约 1-2 分钟'); },
  // ---------- 撤销 ----------
  async undo(tok) { tok = tok || this.lastUndo; if (!tok) return this.toast('没有可撤销的操作', true);
    try { const r = await this.post('/api/undo/' + tok, {}); this.lastUndo = null; this.toast(r.message); await this.refresh(); const a = this.activeTab(); if (a) this.loadTab(a); } catch (e) { this.toast(e.message, true); } },
  undoToast(r) { this.lastUndo = r.undo; this.toast(r.message + '（Ctrl+Z 撤销）', false, {label: '撤销', run: () => this.undo(r.undo)}); },
  delSource(s) { this.api('/api/sources/' + s.id + '/undoable', {method: 'DELETE'}).then(async r => { this.undoToast(r); this.srcs = await this.api('/api/sources'); }).catch(e => this.toast(e.message, true)); },
  // ---------- 批量操作 ----------
  async bulk(action, extra = {}) { const ids = [...this.checked]; if (!ids.length) return;
    try { const r = await this.post('/api/products/bulk', {ids, action, ...extra}); if (action === 'delete') this.checked = []; this.undoToast(r); await this.refresh(); } catch (e) { this.toast(e.message, true); } },
  bulkCategory() { this.prompt('批量修改类目', `把选中的 ${this.checked.length} 个商品归入类目：`, '', v => this.bulk('category', {value: v})); },
  exportCsv(only) { const rows = only ? this.all.filter(p => this.checked.includes(p.id)) : this.all; const head = ['id','platform','title','category','chain','brand','last_price','currency','cost'];
    const esc = v => '"' + String(v ?? '').replace(/"/g, '""') + '"'; const csv = '\ufeff' + [head.join(','), ...rows.map(p => head.map(h => esc(p[h])).join(','))].join('\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'})); a.download = 'pricehub-products.csv'; a.click(); this.toast('已导出 ' + rows.length + ' 条商品'); },
  checkAll(t, on) { const ids = this.filtered(t).map(p => p.id); this.checked = on ? [...new Set([...this.checked, ...ids])] : this.checked.filter(i => !ids.includes(i)); },
  // ---------- 虚拟滚动 ----------
  filtered(t) { const f = t.f, key = JSON.stringify([f, t.sk, t.asc, this.allVer || 0]); const m = MEMO['p' + t.id];
    if (m && m.key === key) return m.rows; const q = f.q.toLowerCase();
    const rows = this.all.filter(p => (!q || (p.title + ' ' + (p.brand || '')).toLowerCase().includes(q)) && (!f.platform || p.platform === f.platform) && (!f.category || p.category === f.category))
      .sort((a, b) => { const g = p => t.sk === 'conf' ? (p.last_conf || {}).confidence : p[t.sk]; const x = g(a) ?? '', y = g(b) ?? ''; return (x > y ? 1 : x < y ? -1 : 0) * (t.asc ? 1 : -1); });
    MEMO['p' + t.id] = {key, rows}; return rows; },
  vwin(t, rows, rh = 28) { const s = Math.max(0, Math.floor((t.st || 0) / rh) - 8), e = Math.min(rows.length, s + Math.ceil((t.vh || 900) / rh) + 16); return {s, e, top: s * rh, bot: (rows.length - e) * rh}; },
  vrows(t, rows) { const w = this.vwin(t, rows); return rows.slice(w.s, w.e); },
  onScroll(t, el) { t.st = el.scrollTop; t.vh = el.clientHeight; },
  // ---------- 平台目录（一次加载 920 条，本地筛选 + 虚拟滚动） ----------
  async loadCatalog(t, force) { if (!this.pAll || force) { t.loading = true; try { this.pAll = (await this.api('/api/platforms?limit=5000')).items; this.pVer = (this.pVer || 0) + 1; } catch (e) { this.toast(e.message, true); } t.loading = false; }
    t.cat = {total: this.catRows(t).length}; },
  catRows(t) { const key = JSON.stringify([t.country, t.ptype, t.q, t.region, t.status, t.strategy, this.pVer]); const m = MEMO['c' + t.id]; if (m && m.key === key) return m.rows; const q = (t.q || '').toLowerCase();
    const r = (this.pAll || []).filter(p => (!t.country || p.country === t.country) && (!t.region || p.region === t.region) && (!t.status || (p.status || '未检测') === t.status) && (!t.ptype || (p.type || '').includes(t.ptype))
      && (!t.strategy || (p.strategy || '').startsWith(t.strategy)) && (!q || [p.name, p.domain, p.note, p.country].some(x => (x || '').toLowerCase().includes(q))));
    MEMO['c' + t.id] = {key, rows: r}; return r; },
  openCatalog(f) { const t = this.openMod('catalog'); Object.assign(t, {country: '', ptype: '', q: '', region: '', status: '', strategy: '', st: 0}, f); const el = document.querySelector('[data-view=catalog] .vscroll'); el && (el.scrollTop = 0); this.loadCatalog(t); },
  stLabel(s) { return ST_LABEL[s] || s || '未检查'; },
  statusBadge(s) { const [ic, col] = STATUS[s] || ['circle-outline', '#8b8b8b']; return `<span class="st" style="color:${col}"><i class="codicon codicon-${ic}"></i>${ESC(ST_LABEL[s] || s || '未检查')}</span>`; },
  stratLabel(s) { return (s || '').startsWith('专属') ? '专属适配' : s === '通用' ? '通用识别' : (s || '—'); },
  async explainPlatform(p) { try { this.xp = {id: p.id, ...(await this.api(`/api/platforms/${p.id}/explain`))}; } catch (e) { this.xp = null; } },
  select(kind, obj) { this.sel = {kind, obj}; if (kind === 'platform') this.explainPlatform(obj); },
  async togglePlatform(p, force) { const bad = ['反爬拦截', '不可达', '需JS渲染', '已关停', '域名失效'];
    if (!p.enabled && !force && bad.includes(p.status)) { const x = await this.api(`/api/platforms/${p.id}/explain`);
      this.dlg = {title: '启用前请注意：' + p.name, lines: [`<div class="explain"><h5><i class="codicon codicon-warning"></i>${ESC(x.title)}</h5><div class="why">${ESC(x.reason)}</div><b>建议下一步：</b><ol>${x.next.map(n => '<li>' + ESC(n) + '</li>').join('')}</ol></div>`, '仍然启用的话，定时监控会尝试读取，但很可能失败。'], okLabel: '仍然启用', ok: () => this.togglePlatform(p, true)}; return; }
    try { const r = await this.post(`/api/platforms/${p.id}/${p.enabled ? 'disable' : 'enable'}`, {}); p.enabled = p.enabled ? 0 : 1; if (r.source_id) p.source_id = r.source_id;
      this.toast((p.enabled ? '已启用：' : '已停用：') + p.name); this.loadPstats(); this.ptree = await this.api('/api/platforms/tree'); this.srcs = await this.api('/api/sources'); } catch (e) { this.toast(e.message, true); } },
  async bulkEnable(t) { try { const r = await this.post('/api/platforms/bulk_enable', {country: t.country}); this.toast(`已启用「${t.country}」${r.enabled} 个平台`); (this.pAll || []).forEach(p => p.country === t.country && p.domain && (p.enabled = 1)); this.loadPstats(); this.ptree = await this.api('/api/platforms/tree'); } catch (e) { this.toast(e.message, true); } },
  async refresh() { try { const [d, a, s, ps, pt] = await Promise.all([this.api('/api/dashboard'), this.api('/api/products'), this.api('/api/sources'), this.api('/api/platforms/stats'), this.api('/api/platforms/tree')]);
    this.dash = d; this.all = a; this.allVer = (this.allVer || 0) + 1; this.srcs = s; this.pstats = ps; this.ptree = pt; this.loaded = true; } catch (e) { this.toast(e.message, true); } },
  async loadTab(t) { try {
    if (t.type === 'dashboard') { this.dash = await this.api('/api/dashboard'); this.$nextTick(() => this.dashCharts(t)); }
    if (t.type === 'products' || t.type === 'compare') { this.all = await this.api('/api/products'); this.allVer = (this.allVer || 0) + 1; this.loaded = true; }
    if (t.type === 'sources') this.srcs = await this.api('/api/sources');
    if (t.type === 'catalog') await this.loadCatalog(t);
    if (t.type === 'alerts') this.alertsList = await this.api('/api/alerts');
    if (t.type === 'ai') this.providers = await this.api('/api/ai/providers');
    if (t.type === 'product' && !t.d) await this.loadProduct(t);
    if (t.type === 'deep' && !t.d) await this.loadDeep(t);
  } catch (e) { this.toast(e.message, true); } },
  // ---------- AI 设置 ----------
  aiSettings(prov) { const p = prov || this.settings.ai_provider || 'deepseek'; this.dlg = {title: '设置 AI 服务', lines: ['选择 AI 服务商并填写 API 密钥，保存后立即生效（密钥只保存在本机数据库）。',
      `当前状态：${Object.entries(this.providers).map(([k, v]) => k + (v.configured ? '（已填写）' : '（未填写）')).join('，')}`], input: '', inputLabel: p + ' 的 API 密钥（留空表示清除）', ok: async v => {
      try { this.settings = await this.post('/api/settings', {ai_provider: p, ai_key: v || ''}); this.providers = await this.api('/api/ai/providers'); this.toast(v ? '已保存 ' + p + ' 密钥' : '已清除 ' + p + ' 密钥'); } catch (e) { this.toast(e.message, true); } }}; },
  keys(e) { const c = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
    if (c && !e.shiftKey && k === 'z') { const el = document.activeElement; if (!(el && /INPUT|TEXTAREA/.test(el.tagName))) { e.preventDefault(); return this.undo(); } }
    if (c && !e.shiftKey && k === 'm') { e.preventDefault(); return this.openQuickMonitor(); }
    if (e.key === 'Escape') { this.sug = null; this.showTasks = false; if (this.qmOpen) { this.qmOpen = false; return; } }
    return this.keysBase(e); },
};
function App() { const b = AppBase(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V4)); return b; }
