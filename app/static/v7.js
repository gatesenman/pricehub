// v7：团队协作——登录页、工作区切换、成员与邀请、用量与配额。单机模式（未开启团队模式）下免登录，使用默认工作区。
Object.assign(MODS, {team: ['organization', '团队与成员'], usage: ['dashboard', '用量与配额']});
const ROLE_CN = {owner: '所有者', admin: '管理员', editor: '编辑', viewer: '只读'};
const METRIC_CN = {skus: '监控商品数', collects: '本月采集次数', api_calls: '今日接口调用'};
const V7 = {
  me: {workspace: {id: 1, name: '默认工作区'}, workspaces: [], logged_in: false},
  login: {open: false, mode: 'login', email: '', password: '', name: '', invite: '', inv: null, error: '', sso: null, busy: false},
  wsMenu: false,
  roleCn(r) { return ROLE_CN[r] || r || ''; }, metricCn(m) { return METRIC_CN[m] || m; },
  async loadMe() { try { this.me = await this.api('/api/auth/me'); } catch (e) {} return this.me; },
  async openLogin(err) { const q = new URLSearchParams(location.search); const L = this.login; L.open = true; L.error = err || q.get('login_error') || ''; L.invite = q.get('invite') || '';
    try { L.sso = await this.api('/api/auth/oidc/config'); } catch (e) { L.sso = null; }
    if (L.invite) { try { L.inv = await this.api('/api/invites/preview?token=' + encodeURIComponent(L.invite)); L.email = L.inv.email || ''; L.mode = 'register'; } catch (e) { L.error = e.message; } } },
  async doLogin() { const L = this.login; L.error = ''; L.busy = true;
    try { if (L.mode === 'register') await this.post('/api/auth/register', {email: L.email, name: L.name, password: L.password, invite: L.invite || undefined});
      else await this.post('/api/auth/login', {email: L.email, password: L.password});
      if (L.invite && L.mode === 'login') { try { await this.post('/api/invites/accept', {token: L.invite}); } catch (e) {} }
      location.href = '/'; } catch (e) { L.error = e.message; } finally { L.busy = false; } },
  ssoLogin() { location.href = '/api/auth/oidc/login' + (this.login.invite ? '?invite=' + encodeURIComponent(this.login.invite) : ''); },
  async logout() { await this.post('/api/auth/logout', {}); location.href = '/'; },
  async switchWs(id) { this.wsMenu = false; if (id === this.me.workspace.id) return; await this.post('/api/auth/switch', {ws_id: id}); location.reload(); },
  async newWs() { const n = prompt('新工作区名称'); if (!n) return; try { await this.post('/api/workspaces', {name: n, plan: 'free'}); location.reload(); } catch (e) { this.toast(e.message, true); } },
  canAdmin() { return !this.me.team_mode || ['owner', 'admin'].includes(this.me.role); },
  async loadV7(t) { const A = u => this.api(u);
    if (t.type === 'team') { await this.loadMe(); [t.m, t.oidc] = await Promise.all([this.me.logged_in ? A('/api/workspace/members') : Promise.resolve({items: [], invites: [], roles: Object.keys(ROLE_CN)}), A('/api/auth/oidc/config').catch(() => ({}))]); t.inv = null; }
    if (t.type === 'usage') { t.u = await A('/api/usage'); t.q = Object.fromEntries((t.u.items || []).map(x => [x.metric, x.limit ?? ''])); } },
  async setTeamMode(t, on) { try { await this.post('/api/auth/mode', {enabled: on}); this.toast(on ? '已开启团队模式：之后需要登录才能使用' : '已关闭团队模式：本机免登录使用默认工作区'); await this.loadV7(t); } catch (e) { this.toast(e.message, true); } },
  async registerOwner(t) { try { await this.post('/api/auth/register', {email: t.oe, name: t.on, password: t.op}); this.toast('已创建账号，你是默认工作区的所有者'); await this.loadV7(t); } catch (e) { this.toast(e.message, true); } },
  async sendInvite(t) { try { const r = await this.post('/api/workspace/invites', {email: t.ie, role: t.ir}); t.inv = location.origin + r.invite_path; t.ie = ''; await this.loadV7(t); t.inv = location.origin + r.invite_path; } catch (e) { this.toast(e.message, true); } },
  async setRole(t, m, role) { try { await this.post('/api/workspace/members/' + m.id, {role}, 'PUT'); this.toast('已修改角色'); } catch (e) { this.toast(e.message, true); } await this.loadV7(t); },
  async removeMember(t, m) { if (!confirm('确定把 ' + m.email + ' 移出工作区？')) return; try { await this.api('/api/workspace/members/' + m.id, {method: 'DELETE'}); } catch (e) { this.toast(e.message, true); } await this.loadV7(t); },
  async revokeInvite(t, i) { await this.api('/api/workspace/invites/' + i.id, {method: 'DELETE'}); await this.loadV7(t); },
  async saveOidc(t) { try { await this.post('/api/auth/oidc/config', t.oidc); this.toast('已保存单点登录设置'); } catch (e) { this.toast(e.message, true); } },
  async saveQuota(t) { try { await this.post('/api/workspace/settings', {plan: t.u.plan, quota: {max_skus: t.q.skus, collects_per_month: t.q.collects, api_calls_per_day: t.q.api_calls}}, 'PUT'); this.toast('已保存套餐与配额'); await this.loadV7(t); } catch (e) { this.toast(e.message, true); } },
  pct(x) { return x.limit ? Math.min(100, Math.round(x.used * 100 / x.limit)) : 0; },
};
(() => { const base = App; window.App = function () { const b = base(); Object.defineProperties(b, Object.getOwnPropertyDescriptors(V7));
  const lt = b.loadTab, om = b.openMod, init = b.init;
  b.loadTab = async function (t) { if (['team', 'usage'].includes(t.type)) { try { await this.loadV7(t); } catch (e) { this.toast(e.message, true); } return; } await lt.call(this, t); };
  b.openMod = function (type, focus = true) { if (!['team', 'usage'].includes(type)) return om.call(this, type, focus); const ex = this.tabs.find(t => t.type === type);
    if (ex) { if (focus) this.active = ex.id; return ex; }
    return this.newTab(type, type === 'team' ? {m: {items: [], invites: []}, oidc: {}, ie: '', ir: 'editor', inv: null, oe: '', on: '', op: ''} : {u: {items: [], plans: {}}, q: {}}, focus); };
  b.init = async function () { const me = await this.loadMe(); const q = new URLSearchParams(location.search);
    if (me.need_login || q.get('invite') && !me.logged_in && me.team_mode) { this.obOpen = false; return this.openLogin(); }
    if (q.get('invite') && me.logged_in) { try { await this.post('/api/invites/accept', {token: q.get('invite')}); history.replaceState(null, '', '/'); location.reload(); return; } catch (e) { this.toast(e.message, true); } }
    await init.call(this); };
  return b; }; App.confBadge = base.confBadge; App.money = base.money; })();
