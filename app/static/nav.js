// 侧栏导航重构：活动栏（工作台 / 商品 / 平台目录 / 设置）+ 按用途分组；筛选节点与打开页面节点区分。
// 用词：「来源平台」= 商品所在的电商平台；「平台目录」= 内置的 920 个平台。
const NAV_GROUPS = {
  work: [['mon', '监控', ['dashboard', 'products', 'alerts', 'rules']], ['ana', '分析', ['compare', 'deep', 'match', 'reports']],
         ['dec', '决策', ['pricing', 'map']], ['srch', '搜索', ['search', 'ai']], ['data', '数据', ['sources', 'official', 'catalog']]],
  settings: [['set', '设置', ['admin', 'nodes']], ['teamg', '团队', ['team', 'usage']]],
};
const NAV = {
  navView: localStorage.getItem('ph-nav') || 'work', catDim: localStorage.getItem('ph-catdim') || 'region', navQ: '', favs: JSON.parse(localStorage.getItem('ph-fav') || '[]'),
  navViews: [{id: 'work', icon: 'layers', label: '工作台', tip: '工作台：按用途打开各功能模块'}, {id: 'goods', icon: 'package', label: '我的商品', tip: '我的商品：按来源平台、类目、产业链筛选'},
             {id: 'catalog', icon: 'globe', label: '平台目录', tip: '平台目录：浏览内置的电商平台'}, {id: 'settings', icon: 'settings-gear', label: '设置', tip: '设置：集成、团队、用量'}],
  catDims: [['region', '大洲'], ['country', '国家'], ['type', '类型'], ['status', '状态']],
  setNav(id) { if (this.navView === id && this.showSide) this.showSide = false; else { this.navView = id; this.showSide = true; } localStorage.setItem('ph-nav', this.navView); },
  setCatDim(d) { this.catDim = d; localStorage.setItem('ph-catdim', d); },
  toggleFavs(ids) { const s = new Set(this.favs); const allIn = ids.every(i => s.has(i)); ids.forEach(i => allIn ? s.delete(i) : s.add(i)); this.favs = [...s]; localStorage.setItem('ph-fav', JSON.stringify(this.favs)); this.toast(allIn ? '已取消收藏' : `已收藏 ${ids.length} 个商品`); },
  modNode(k) { if (k === 'deep') return {label: '深度对比', icon: 'split-horizontal', kind: 'open', tip: '先在商品表勾选 2 个以上商品', run: () => this.checked.length >= 2 ? this.openDeep(this.checked) : this.toast('请先在商品监控里勾选 2 个以上商品', true)};
    const m = MODS[k]; return m && {label: m[1], icon: m[0], mod: k, kind: 'open', run: () => this.openMod(k)}; },
  filterP(k, v) { const t = this.openMod('products'); t.f = {q: '', platform: '', category: '', chain: '', fav: false, [k]: v}; t.pg = 1; },
  filterProducts(k, v) { this.filterP(k, v); },
  isActiveFilter(n) { if (!n.fk) return false; const t = this.activeTab(); if (!t) return false;
    if (n.fk[0] === 'cat') return t.type === 'catalog' && t[n.fk[1]] === n.fk[2]; return t.type === 'products' && t.f && t.f[n.fk[0]] === n.fk[1]; },
  filterChips(t) { const f = t.f || {}, L = {platform: '来源平台', category: '类目', chain: '产业链'}; const out = Object.keys(L).filter(k => f[k]).map(k => ({k, label: L[k] + '：' + f[k]}));
    if (f.fav) out.push({k: 'fav', label: '仅收藏'}); return out; },
  navGroups() { const q = (this.navQ || '').trim().toLowerCase(); const keep = n => !q || n.label.toLowerCase().includes(q);
    let G = [];
    if (this.navView === 'work' || this.navView === 'settings') {
      G = NAV_GROUPS[this.navView].map(([key, label, ks]) => ({key: 'w_' + key, label, children: ks.map(k => this.modNode(k)).filter(Boolean)}));
      if (this.navView === 'work') { const used = new Set(Object.values(NAV_GROUPS).flatMap(g => g.flatMap(x => x[2]))); const rest = Object.keys(MODS).filter(k => !used.has(k)).map(k => this.modNode(k));
        if (rest.length) G.push({key: 'w_other', label: '其他', children: rest}); }
    } else if (this.navView === 'goods') {
      const cnt = k => this.all.reduce((m, p) => { const v = p[k]; if (v) m[v] = (m[v] || 0) + 1; return m; }, {});
      const mk = (k, icon) => { const c = cnt(k); return Object.keys(c).sort().map(v => ({label: v, icon, kind: 'filter', fk: [k, v], count: c[v], countTip: `${c[v]} 个商品`, run: () => this.filterP(k, v)})); };
      G = [{key: 'g_all', label: '我的商品', children: [{label: '全部商品', icon: 'list-unordered', kind: 'filter', count: this.all.length, countTip: '商品总数', run: () => this.filterP('q', '')},
              {label: '已收藏', icon: 'star-full', kind: 'filter', fk: ['fav', true], count: this.favs.length, countTip: '在商品表勾选后点「收藏」', run: () => this.filterP('fav', true)}]},
           {key: 'g_plat', label: '按来源平台', children: mk('platform', 'symbol-namespace'), empty: '还没有商品'}, {key: 'g_cat', label: '按类目', children: mk('category', 'symbol-folder')},
           {key: 'g_chain', label: '按产业链', children: mk('chain', 'type-hierarchy-sub')}];
    } else {
      const T = this.ptree || {}, S = this.pstats || {};
      const node = (field, icon, x) => ({label: x.k, icon, kind: 'filter', fk: ['cat', field, x.k], count: x.e != null ? `${x.e} / ${x.n}` : `${x.n}`,
                    countTip: x.e != null ? `已启用 ${x.e} / 共 ${x.n} 个平台` : `共 ${x.n} 个平台`, run: () => this.openCatalog({[field]: x.k})});
      const d = this.catDim;
      const items = d === 'region' ? (T.regions || []).map(x => node('region', 'globe', x)) : d === 'country' ? (T.countries || []).map(x => node('country', 'location', x))
        : d === 'type' ? (T.types || []).map(x => node('ptype', 'tag', x)) : (S.by_status || []).map(x => ({...node('status', (STATUS[x.k] || ['circle-outline'])[0], x), color: (STATUS[x.k] || [])[1]}));
      G = [{key: 'c_' + d, label: (this.catDims.find(x => x[0] === d) || [])[1] + '（平台目录共 ' + (S.total || 0) + ' 个）', children: items}];
    }
    return G.map(g => ({...g, children: g.children.filter(keep)})).filter(g => !q || g.children.length); },
  tree() { return this.navGroups(); },
};
(() => { const base = App; window.App = function () { const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(NAV)); return b; }; App.confBadge = base.confBadge; App.money = base.money; })();
