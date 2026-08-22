// macro-view.js — 全局左侧专题导航（timsun 风格）+ 宏观详情 iframe（懒加载缓存）

// ── 全站专题导航 ──
// 结构：核心入口（视图级）+ 全部专题（分组，可展开/收起）；与网站页面一一对应
const NAV = [
  { key: 'assets', label: '大类资产', page: '/assets/', items: [
    { key: 'assets/equities', label: '美股', page: '/assets/equities.html' },
    { key: 'assets/etfs', label: 'ETF 看板', page: '/assets/etfs.html' },
    { key: 'assets/options', label: '期权 / GEX', page: '/assets/options.html' },
    { key: 'assets/positioning', label: '持仓追踪 · CFTC', page: '/assets/positioning.html' },
    { key: 'assets/bonds', label: '债券', page: '/assets/bonds.html' },
    { key: 'assets/commodities', label: '商品', page: '/assets/commodities.html' },
    { key: 'assets/fx', label: '外汇', page: '/assets/fx.html' },
    { key: 'assets/crypto', label: '加密货币', page: '/assets/crypto.html' },
    { key: 'assets/crypto-derivatives', label: '衍生品 · OKX+Deribit', page: '/assets/crypto-derivatives.html' },
  ]},
  { key: 'rates', label: '利率', page: '/rates/', items: [
    { key: 'rates/fed-funds', label: '联邦基金利率', page: '/rates/fed-funds.html' },
    { key: 'rates/yield-curve', label: '收益率曲线', page: '/rates/yield-curve.html' },
    { key: 'rates/pricing', label: '利率定价', page: '/rates/pricing.html' },
  ]},
  { key: 'inflation', label: '通胀', page: '/inflation/' },
  { key: 'labor', label: '就业', page: '/labor/' },
  { key: 'treasury', label: '美债', page: '/treasury/' },
  { key: 'liquidity', label: '流动性', page: '/liquidity/', items: [
    { key: 'liquidity/transmission-chain', label: '压力指数', page: '/liquidity/transmission-chain.html' },
    { key: 'liquidity/fed-balance-sheet', label: '资产负债表', page: '/liquidity/fed-balance-sheet.html' },
    { key: 'liquidity/operations', label: '公开市场操作', page: '/liquidity/operations.html' },
    { key: 'liquidity/rrp-tga', label: 'RRP & TGA', page: '/liquidity/rrp-tga.html' },
    { key: 'liquidity/reserves', label: '准备金', page: '/liquidity/reserves.html' },
    { key: 'liquidity/global-dollar', label: '全球美元', page: '/liquidity/global-dollar.html' },
    { key: 'liquidity/subsurface', label: '次表层资金流', page: '/liquidity/subsurface.html' },
  ]},
  { key: 'credit', label: '信用', page: '/credit/' },
  { key: 'fed', label: '美联储', page: '/fed/' },
  { key: 'vol', label: '波动率', page: '/volatility/', items: [
    { key: 'volatility/vix', label: 'VIX', page: '/volatility/vix.html' },
  ]},
];

// 核心入口：SPA 视图（switch-tab 事件）或专题页（iframe）
const CORE = [
  { label: '今日判断', view: 'macro' },
  { label: '市场仪表盘', page: '/assets/' },
  { label: '技术分析', view: 'tech' },
  { label: '关联分析', view: 'correlation' },
];

// key ↔ page 双向映射（key 也用于 URL hash / openTopic）
const PAGES = new Map(); // key → page
const KEYS = new Map();  // page → key
NAV.forEach(g => {
  PAGES.set(g.key, g.page);
  KEYS.set(g.page, g.key);
  (g.items || []).forEach(it => { PAGES.set(it.key, it.page); KEYS.set(it.page, it.key); });
});

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ── state ──
let activeUrl = null;   // 当前专题页（iframe）
let navEl = null;
let detailEl = null;
const frames = new Map(); // url → iframe（按需创建，保留各页滚动位置）

function highlight() {
  navEl.querySelectorAll('.macro-nav-item[data-url], .macro-nav-item[data-view]').forEach(a => {
    const on = a.dataset.url ? a.dataset.url === activeUrl : a.dataset.view === activeView;
    a.classList.toggle('active', on);
  });
  // 自动展开选中项所在分组
  [...navEl.querySelectorAll('.macro-nav-item[data-url]')]
    .find(a => a.dataset.url === activeUrl)?.closest('.macro-nav-group')?.classList.add('open');
}

let activeView = null; // 'macro' | 'tech' | 'correlation'

function showUrl(url) {
  let fr = frames.get(url);
  if (!fr) {
    fr = document.createElement('iframe');
    fr.title = url;
    fr.src = url;
    fr.style.display = 'none';
    detailEl.appendChild(fr);
    frames.set(url, fr);
  }
  frames.forEach((f, u) => { f.style.display = u === url ? '' : 'none'; });
}

function selectUrl(url, updateHash = true) {
  activeUrl = url;
  activeView = null;
  highlight();
  if (detailEl) showUrl(url);
  if (updateHash && KEYS.has(url)) history.replaceState(null, '', '#' + KEYS.get(url));
}

function setView(view) {
  activeView = view;
  activeUrl = null;
  highlight();
}

function toggleGroup(grp) {
  grp.classList.toggle('open');
}

// ── 全局导航渲染（index.html 的 #macro-nav）──
export function initGlobalNav() {
  const el = document.getElementById('macro-nav');
  if (!el || navEl) return;
  navEl = el;
  navEl.innerHTML = `
    <div class="macro-nav-brand">
      <div>
        <div class="macro-nav-brand-name">K 线分析</div>
        <div class="macro-nav-brand-sub">美国宏观研究平台</div>
      </div>
      <button class="theme-toggle" id="theme-toggle" title="切换暗色/亮色模式">🌙</button>
    </div>
    <div class="macro-nav-caption">核心入口</div>
    ${CORE.map(c => c.view
      ? `<a class="macro-nav-item core" data-view="${c.view}">${esc(c.label)}</a>`
      : `<a class="macro-nav-item core" data-url="${c.page}">${esc(c.label)}</a>`).join('')}
    <div class="macro-nav-sep"></div>
    <div class="macro-nav-caption">全部专题</div>
    ${NAV.map(g => `
      <div class="macro-nav-group" data-key="${g.key}">
        <div class="macro-nav-group-head">
          <a class="macro-nav-item" data-url="${g.page}">${esc(g.label)}</a>
          ${g.items ? '<button class="macro-nav-toggle" title="展开 / 收起">›</button>' : ''}
        </div>
        ${g.items ? `<div class="macro-nav-sub">${g.items.map(it =>
          `<a class="macro-nav-item child" data-url="${it.page}">${esc(it.label)}</a>`).join('')}</div>` : ''}
      </div>`).join('')}
    <div class="macro-nav-foot">
      <span>美东时间</span><span id="macro-nav-clock"></span>
    </div>
  `;

  navEl.addEventListener('click', e => {
    const a = e.target.closest('a.macro-nav-item');
    if (a) {
      e.preventDefault();
      if (a.dataset.view) { // SPA 视图切换
        setView(a.dataset.view);
        window.dispatchEvent(new CustomEvent('switch-tab', { detail: a.dataset.view }));
      } else if (a.dataset.url !== activeUrl) {
        // 专题页 iframe 位于宏观视图：先切过去再加载
        window.dispatchEvent(new CustomEvent('switch-tab', { detail: 'macro' }));
        selectUrl(a.dataset.url);
      }
      closeDrawer();
      return;
    }
    const t = e.target.closest('.macro-nav-toggle');
    if (t) toggleGroup(t.closest('.macro-nav-group'));
  });

  // 外部切视图（dashboard 小卡片、openTopic 等）时同步高亮
  window.addEventListener('switch-tab', e => {
    if (e.detail !== 'macro') setView(e.detail);
  });

  window.addEventListener('hashchange', () => {
    const page = PAGES.get(location.hash.slice(1));
    if (page && page !== activeUrl) selectUrl(page, false);
  });

  // 美东时间（每分钟更新）
  function tick() {
    const el2 = document.getElementById('macro-nav-clock');
    if (!el2) return;
    el2.textContent = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date());
  }
  tick();
  setInterval(tick, 60000);

  // 窄屏抽屉
  const toggle = document.getElementById('macro-nav-toggle');
  const backdrop = document.getElementById('macro-nav-backdrop');
  toggle?.addEventListener('click', () =>
    document.body.classList.toggle('side-open'));
  backdrop?.addEventListener('click', closeDrawer);
}

function closeDrawer() {
  document.body.classList.remove('side-open');
}

// ── 宏观详情（右侧 iframe 区）──
function renderDetail(container) {
  detailEl = document.createElement('div');
  detailEl.className = 'macro-detail';
  container.appendChild(detailEl);

  const initialKey = location.hash.slice(1);
  selectUrl(PAGES.get(initialKey) || NAV[0].page, false);
}

// ── init ──
export function initMacroView() {
  const container = document.getElementById('macro-chart-card');
  container.innerHTML = '';
  renderDetail(container);
}

// 外部跳转指定专题（如仪表盘净流动性小卡片）：未初始化靠 hash 兜底
export function openTopic(id) {
  const page = PAGES.get(id);
  if (page && detailEl) selectUrl(page);
  else location.hash = id;
}

// ── status ──
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = `${NAV.length} 专题 · ${PAGES.size} 页面`;
  document.getElementById('status-range').textContent = '';
}
