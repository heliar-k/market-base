// 专题导航注入 — 新专题页只需 <div class="re-nav" id="re-nav"></div> + 本脚本；
// 改导航结构只改这里（rates/volatility 两套 + 专题间 Tab；fed/credit/treasury 已合并单页）
(function () {
  // 每个专题的入口页（link-card 网格已承载子页导航）
  // 子页顶部只渲染首项“返回入口”链接
  var NAVS = {
    '/rates/': [['概览', 'index.html']],
    '/volatility/': [['全景仪表盘', 'index.html']],
  };
  // 专题间 Tab（总入口）
  var TABS = [
    ['利率', '/rates/'],
    ['通胀', '/inflation/'],
    ['就业', '/labor/'],
    [ '美债', '/treasury/' ],
    ['流动性', '/liquidity/'],
    ['信用', '/credit/'],
    ['美联储', '/fed/'],
    ['波动率', '/volatility/'],
  ];

  // 内嵌（SPA 宏观分栏的 iframe）时不渲染主题按钮与专题间 Tab/仪表盘链接
  var embedded = window.self !== window.top;
  if (embedded) document.body.classList.add('embedded');

  var el = document.getElementById('re-nav');
  if (!el) return;
  var path = location.pathname;
  // 专题入口页（以 / 或 /index.html 结尾）；子页由 NAVS 循环另行处理
  var isIndex = path.slice(-1) === '/' || path.slice(-11) === '/index.html';
  var html = '';
  for (var dir in NAVS) {
    if (path.indexOf(dir) === 0) {
      var base = path.slice(0, path.indexOf(dir) + dir.length);
      if (!isIndex) {
        // 子页只留返回入口链接；入口页由 link-card 网格承担子页导航，顶部不重复
        html += '<a href="' + base + NAVS[dir][0][1] + '">← ' + NAVS[dir][0][0] + '</a>';
      }
      break;
    }
  }
  if (!embedded) {
    TABS.forEach(function (t) {
      html += '<a href="' + t[1] + '">' + t[0] + '</a>';
    });
    html += '<a href="/">← 仪表盘</a>';
  }
  if (html) {
    el.innerHTML = html;
  } else {
    el.style.display = 'none';
  }

  // sticky 页内导航条高度随换行变化（44px 一行 / 79px+ 两行）→ 实时测量
  // 并设置滚动偏移，锚点跳转目标始终落在导航条下方 12px
  var toc = document.querySelector('.page-toc');
  if (toc) {
    new ResizeObserver(function () {
      document.documentElement.style.scrollPaddingTop = (toc.offsetHeight + 12) + 'px';
    }).observe(toc);
  }

  // 主题：默认实时跟随系统；点悬浮按钮后仅本会话固定（刷新恢复自动）。
  // 内嵌 iframe 由父页 SPA 驱动（DARK_KEY + storage 事件）
  var DARK_KEY = 'ticker-toolkit-dark';
  var manual = false;
  function resolveDark() {
    if (embedded) return localStorage.getItem(DARK_KEY) === '1';
    if (manual) return document.body.classList.contains('dark');
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function applyTheme() {
    document.body.classList.toggle('dark', resolveDark());
  }
  applyTheme();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
    if (!embedded && !manual) {
      applyTheme();
      window.dispatchEvent(new Event('theme-changed'));
    }
  });
  window.addEventListener('storage', function (e) {
    if (e.key !== DARK_KEY) return;
    applyTheme();
    // 图表按主题重绘（rates-common 监听该事件）
    window.dispatchEvent(new Event('theme-changed'));
  });

  // 返回顶部：专题入口页右下角悬浮，滚动超过一屏才显示（内嵌 iframe 同样生效）
  if (isIndex) {
    var topBtn = document.createElement('button');
    topBtn.className = 'back-top';
    topBtn.title = '返回顶部';
    topBtn.textContent = '↑';
    topBtn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    document.body.appendChild(topBtn);
    var syncTop = function () { topBtn.classList.toggle('show', window.scrollY > 300); };
    window.addEventListener('scroll', syncTop, { passive: true });
    syncTop();
  }

  // 悬浮切换按钮仅独立访问时显示（内嵌时由父页统一控制）
  if (embedded) return;
  var tb = document.createElement('button');
  tb.className = 'theme-float';
  tb.title = '切换亮暗主题';
  tb.textContent = resolveDark() ? '☀️' : '🌙';
  tb.addEventListener('click', function () {
    manual = true; // 会话级覆盖：停止跟随系统，刷新恢复自动
    document.body.classList.toggle('dark');
    tb.textContent = document.body.classList.contains('dark') ? '☀️' : '🌙';
    // rates-common 等已监听该事件，图表随之重绘
    window.dispatchEvent(new CustomEvent('theme-changed', { detail: { dark: document.body.classList.contains('dark') } }));
  });
  document.body.appendChild(tb);
})();
