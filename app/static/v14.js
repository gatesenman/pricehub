// v14：首屏加速与桌面观感——启动闪屏、Chart.js 按需加载、品牌菜单栏。
// 注意：只包装已有 init（保留 v7 团队登录逻辑），不要整段替换。
(function () {
  const BOOT = document.getElementById('ph-boot');
  function hideBoot() {
    if (!BOOT || BOOT.dataset.done) return;
    BOOT.dataset.done = '1';
    BOOT.classList.add('hide');
    const app = document.querySelector('.app');
    if (app) app.classList.add('ready');
    setTimeout(() => BOOT.remove(), 280);
  }
  window.__phHideBoot = hideBoot;
  document.addEventListener('alpine:initialized', () => setTimeout(hideBoot, 30));
  setTimeout(hideBoot, 8000);

  let chartLoading = null;
  window.ensureChart = function ensureChart() {
    if (typeof Chart !== 'undefined') return Promise.resolve(Chart);
    if (chartLoading) return chartLoading;
    chartLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/chart.js';
      s.async = true;
      s.onload = () => resolve(Chart);
      s.onerror = () => reject(new Error('Chart.js 加载失败'));
      document.head.appendChild(s);
    });
    return chartLoading;
  };

  const _draw = window.drawChart;
  window.drawChart = function (id, cfg) {
    if (typeof Chart === 'undefined') {
      ensureChart().then(() => {
        if (typeof _draw === 'function') _draw(id, cfg);
        else {
          const el = document.getElementById(id); if (!el) return;
          if (charts[id]) charts[id].destroy();
          charts[id] = new Chart(el, cfg);
        }
      }).catch(() => {});
      return;
    }
    if (typeof _draw === 'function') return _draw(id, cfg);
    const el = document.getElementById(id); if (!el) return;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(el, cfg);
  };
})();

(() => {
  const base = App;
  window.App = function () {
    const b = base();
    const prevInit = b.init;
    const prevDash = b.dashCharts;
    const prevPrice = b.priceChart;
    const prevDeep = b.deepChart;
    b.bootReady = false;
    b.init = async function () {
      document.documentElement.dataset.theme = this.theme;
      // 尽早揭开闪屏，让菜单栏可见；真正的数据加载仍走原 init（含 v7 登录）
      this.bootReady = true;
      window.__phHideBoot && window.__phHideBoot();
      await prevInit.call(this);
      this.bootReady = true;
      window.__phHideBoot && window.__phHideBoot();
    };
    if (prevDash) b.dashCharts = function (t) {
      ensureChart().then(() => prevDash.call(this, t)).catch(() => {});
    };
    if (prevPrice) b.priceChart = function (t) {
      ensureChart().then(() => prevPrice.call(this, t)).catch(() => {});
    };
    if (prevDeep) b.deepChart = function (t) {
      ensureChart().then(() => prevDeep.call(this, t)).catch(() => {});
    };
    return b;
  };
  App.confBadge = base.confBadge;
  App.money = base.money;
})();
