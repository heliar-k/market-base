// 专题导航注入 — 新专题页只需 <div class="re-nav" id="re-nav"></div> + 本脚本；
// 改导航结构只改这里（rates/volatility 两套 + 专题间 Tab；fed/credit/treasury 已合并单页）
(function () {
  // 每个专题的子页导航（相对该专题目录）
  var NAVS = {
    '/rates/': [
      ['联邦基金利率', 'fed-funds.html'],
      ['收益率曲线', 'yield-curve.html'],

      ['实际利率', 'real-rates.html'],
      ['利率预期', 'expectations/'],
    ],
    '/volatility/': [
      ['全景仪表盘', 'index.html'],
      ['VIX 详情', 'vix.html'],
    ],
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
  var html = '';
  for (var dir in NAVS) {
    if (path.indexOf(dir) === 0) {
      var base = path.slice(0, path.indexOf(dir) + dir.length);
      NAVS[dir].forEach(function (item) {
        html += '<a href="' + base + item[1] + '">' + item[0] + '</a>';
      });
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

  // 主题：以 localStorage 为准（与 SPA 共享 key）；无手动偏好时跟随系统，系统切换实时生效
  var DARK_KEY = 'ticker-toolkit-dark';
  function resolveDark() {
    var s = localStorage.getItem(DARK_KEY);
    if (s !== null) return s === '1';
    return !embedded && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function applyTheme() {
    document.body.classList.toggle('dark', resolveDark());
  }
  applyTheme();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
    if (localStorage.getItem(DARK_KEY) === null) {
      applyTheme();
      window.dispatchEvent(new Event('theme-changed'));
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 't' && e.metaKey) {
      document.body.classList.toggle('dark');
      localStorage.setItem(DARK_KEY, document.body.classList.contains('dark') ? '1' : '0');
      window.dispatchEvent(new Event('theme-changed'));
    }
  });
  window.addEventListener('storage', function (e) {
    if (e.key !== DARK_KEY) return;
    applyTheme();
    // 图表按主题重绘（rates-common 监听该事件）
    window.dispatchEvent(new Event('theme-changed'));
  });

  // 悬浮切换按钮仅独立访问时显示（内嵌时由父页统一控制）
  if (embedded) return;
  var tb = document.createElement('button');
  tb.className = 'theme-float';
  tb.title = '切换亮暗主题';
  tb.textContent = resolveDark() ? '☀️' : '🌙';
  tb.addEventListener('click', function () {
    document.body.classList.toggle('dark');
    localStorage.setItem(DARK_KEY, document.body.classList.contains('dark') ? '1' : '0');
    tb.textContent = document.body.classList.contains('dark') ? '☀️' : '🌙';
    // rates-common 等已监听该事件，图表随之重绘
    window.dispatchEvent(new CustomEvent('theme-changed', { detail: { dark: document.body.classList.contains('dark') } }));
  });
  document.body.appendChild(tb);
})();
