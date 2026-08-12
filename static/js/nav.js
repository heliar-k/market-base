// 专题导航注入 — 新专题页只需 <div class="re-nav" id="re-nav"></div> + 本脚本；
// 改导航结构只改这里（rates/fed/volatility/credit 四套 + 专题间 Tab）。
(function () {
  // 每个专题的子页导航（相对该专题目录）
  var NAVS = {
    '/rates/': [
      ['联邦基金利率', 'fed-funds.html'],
      ['收益率曲线', 'yield-curve.html'],
      ['国债拍卖', 'auctions.html'],
      ['实际利率', 'real-rates.html'],
      ['利率预期', 'expectations/'],
    ],
    '/fed/': [
      ['FOMC 声明', 'statements.html'],
      ['官员演讲', 'speeches.html'],
      ['鹰鸽追踪', 'hawkish-dovish.html'],
    ],
    '/volatility/': [['VIX', 'vix.html']],
    '/credit/': [
      ['总览', 'index.html'],
      ['CDS 专题', 'cds.html'],
      ['压力仪表盘', 'stress.html'],
    ],
  };
  // 专题间 Tab（总入口）
  var TABS = [
    ['利率', '/rates/'],
    ['信用', '/credit/'],
    ['美联储', '/fed/'],
    ['波动率', '/volatility/'],
  ];
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
  TABS.forEach(function (t) {
    html += '<a href="' + t[1] + '">' + t[0] + '</a>';
  });
  html += '<a href="/">← 仪表盘</a>';
  el.innerHTML = html;

  // 主题：从 localStorage 恢复（与 SPA 共享 key）+ 悬浮切换按钮
  var DARK_KEY = 'ticker-toolkit-dark';
  var darkOn = localStorage.getItem(DARK_KEY) === '1';
  document.body.classList.toggle('dark', darkOn);
  var tb = document.createElement('button');
  tb.className = 'theme-float';
  tb.title = '切换亮暗主题';
  tb.textContent = darkOn ? '☀️' : '🌙';
  tb.addEventListener('click', function () {
    darkOn = !darkOn;
    document.body.classList.toggle('dark', darkOn);
    localStorage.setItem(DARK_KEY, darkOn ? '1' : '0');
    tb.textContent = darkOn ? '☀️' : '🌙';
    // rates-common 等已监听该事件，图表随之重绘
    window.dispatchEvent(new CustomEvent('theme-changed', { detail: { dark: darkOn } }));
  });
  document.body.appendChild(tb);
})();
