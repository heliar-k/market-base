// tech-view.js — 技术分析视图：结论总览（默认） + K 线与指标图

import { CHART_OPTS, darkChartOpts, addLine, cssVar } from './charts-common.js';
import { loadWatch, saveWatch, removeWatch } from './watchlist.js';

// 转义单源 R.esc（rates-common.js 已在 app/index.html 先于本模块加载；注意顶层 const R 属于全局词法作用域，不挂在 window.R 上）
const esc = R.esc;

// ── state ──────────────────────────────────────────────────────────────────
const SUBVIEW_KEY = 'tech-subview';
let currentSubview = localStorage.getItem(SUBVIEW_KEY) === 'kline' ? 'kline' : 'overview';
let currentSymbol = null;
let currentDiag = null;
let overviewList = null;    // /api/tech/overview 缓存
let activeFilter = 'all';   // all | bullish | bearish | squeeze | oversold | overbought
let klineData = null;       // full dataset from API
let filteredData = null;    // after range filter
let currentDays = 365;
let mainChart, rsiChart, macdChart;
let candleSeries, volumeOverlaySeries;
let maSeries = {};
let rsiSeries, rsiUpper, rsiLower;
let macdHistSeries, macdLineSeries, macdSignalSeries;
let crosshairDate = null;
let debounceTimer = null;
// 调色板全部回读 tokens.css（每次调用重取，主题切换后生效）；键名与 MA 列名对应
const MA_COLORS = () => ({
  MA5: cssVar('--chart-cyan'),
  MA10: cssVar('--color-brand'),
  MA20: cssVar('--color-warn'),
  MA50: cssVar('--color-up'),
  MA60: cssVar('--chart-purple'),
  MA120: cssVar('--color-neutral'),
  MA200: cssVar('--color-down'),
});
const BB_COLOR = () => cssVar('--chart-slate');

const ETF_LIKE = new Set(['SPY', 'QQQ']); // grouped with indices even though type=stock

// ── helpers ────────────────────────────────────────────────────────────────
function scoreTokens(score) {
  if (score > 3) return { text: 'var(--color-up-text)', fill: 'var(--color-up)' };
  if (score < -3) return { text: 'var(--color-down-text)', fill: 'var(--color-down)' };
  return { text: 'var(--color-warn-text)', fill: 'var(--color-warn)' };
}

function rsiToneCls(rsi, neutralCls = '') {
  if (rsi == null) return neutralCls;
  return rsi < 30 ? 'up' : rsi > 70 ? 'down' : neutralCls;
}

function regimeBadgeCls(regime) {
  if (regime === 'bullish' || regime === 'mild_bullish') return 'tag tag-bull';
  if (regime === 'bearish' || regime === 'mild_bearish') return 'tag tag-bear';
  return 'tag tech-ov-tag-neutral';
}

function fmtSigned(v, nd = 0, suffix = '') {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${R.num(n, nd)}${suffix}`;
}

function fmtVolume(v) {
  if (v == null) return '--';
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(v);
}

// 扫描表筛选谓词单源（filterOverviewItems 与 counts 统计共用；超买/超卖与后端 osc_oversold 30/70 对齐）
const SCAN_PREDICATES = {
  all: () => true,
  bullish: (d) => d.regime === 'bullish' || d.regime === 'mild_bullish',
  bearish: (d) => d.regime === 'bearish' || d.regime === 'mild_bearish',
  squeeze: (d) => Boolean(d.BB_squeeze),
  oversold: (d) => (d.RSI != null && d.RSI < 30) || d.STOCH_detail === 'oversold' || d.SMC_pd_zone === 'discount',
  overbought: (d) => (d.RSI != null && d.RSI > 70) || d.STOCH_detail === 'overbought' || d.SMC_pd_zone === 'premium',
};

// ── init ───────────────────────────────────────────────────────────────────
export function initTechView() {
  initSubviewSwitch();
  initCharts();
  initControls();
  initKeyboard();
  initDiagToggle();
  applySubview(currentSubview, false);
  loadSymbols();
  loadTechOverview();
}

// ── subview switch (overview / kline) ──────────────────────────────────────
function initSubviewSwitch() {
  document.querySelectorAll('#tech-subview-switch [data-subview]').forEach(btn => {
    btn.addEventListener('click', () => applySubview(btn.dataset.subview, true));
  });
}

function resizeAllCharts() {
  requestAnimationFrame(() => {
    [mainChart, rsiChart, macdChart].forEach((c, i) => {
      const el = document.getElementById(['main-chart', 'rsi-chart', 'macd-chart'][i]);
      if (c && el && el.clientWidth > 0 && el.clientHeight > 0) {
        c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      }
    });
    if (mainChart && filteredData && filteredData.length) {
      mainChart.timeScale().fitContent();
    }
  });
}

function applySubview(subview, persist = true) {
  currentSubview = subview === 'kline' ? 'kline' : 'overview';
  if (persist) localStorage.setItem(SUBVIEW_KEY, currentSubview);

  document.querySelectorAll('#tech-subview-switch [data-subview]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.subview === currentSubview);
  });

  const ovPane = document.getElementById('tech-ov-pane');
  const klPane = document.getElementById('tech-kline-pane');
  if (ovPane) ovPane.style.display = currentSubview === 'overview' ? 'flex' : 'none';
  if (klPane) klPane.style.display = currentSubview === 'kline' ? 'flex' : 'none';

  if (currentSubview === 'kline') {
    resizeAllCharts();
  }
}

// ── info bar ───────────────────────────────────────────────────────────────
function updateInfoBar() {
  const data = filteredData || klineData;
  const sym = document.getElementById('info-symbol');
  const price = document.getElementById('info-price');
  const change = document.getElementById('info-change');
  const ohlc = document.getElementById('info-ohlc');
  if (!sym) return;
  if (!data || !data.length) {
    sym.textContent = currentSymbol || '--';
    price.textContent = currentDiag?.last_price != null ? currentDiag.last_price.toFixed(2) : '--';
    const chg1d = currentDiag?.changes?.['1d'];
    if (chg1d != null) {
      const up = chg1d >= 0;
      change.textContent = `${up ? '▲' : '▼'} ${up ? '+' : ''}${Number(chg1d).toFixed(2)}%`;
      change.className = 'tech-change ' + (up ? 'up' : 'down');
    } else {
      change.textContent = '--';
      change.className = 'tech-change';
    }
    ohlc.textContent = currentDiag?.regime_label ? `${currentDiag.regime_label} · ${currentDiag.setup_tag || ''}` : '--';
    return;
  }
  const last = data[data.length - 1];
  const prev = data.length >= 2 ? data[data.length - 2] : last;
  const chg = prev.close ? ((last.close - prev.close) / prev.close * 100) : 0;
  const up = chg >= 0;
  sym.textContent = currentSymbol;
  price.textContent = last.close.toFixed(2);
  change.textContent = `${up ? '▲' : '▼'} ${up ? '+' : ''}${chg.toFixed(2)}%`;
  change.className = 'tech-change ' + (up ? 'up' : 'down');
  ohlc.textContent = `O:${last.open.toFixed(2)} H:${last.high.toFixed(2)} L:${last.low.toFixed(2)} V:${fmtVolume(last.volume)}`;
}

// ── diagnosis toggle ───────────────────────────────────────────────────────
function initDiagToggle() {
  const panel = document.getElementById('diag-panel');
  const header = document.getElementById('diag-header');
  header.addEventListener('click', () => {
    panel.classList.toggle('collapsed');
    // wait for CSS transition (0.3s) then force chart resize
    setTimeout(resizeAllCharts, 350);
  });
}

// ── symbols sidebar ────────────────────────────────────────────────────────
// collapse toggle — ResizeObserver on chart containers handles chart resize
const app = document.querySelector('.app');
const sidebarBtn = document.getElementById('sidebar-collapse-btn');
function setSidebarHidden(hidden) {
  app.classList.toggle('sidebar-hidden', hidden);
  sidebarBtn.textContent = hidden ? '▶' : '◀';
  localStorage.setItem('sidebar-hidden', hidden ? '1' : '');
  // 兜底：display:none 即时生效，下一帧容器宽度已变；显式 resize 防部分浏览器 RO 不触发
  resizeAllCharts();
}
sidebarBtn.addEventListener('click', () => setSidebarHidden(!app.classList.contains('sidebar-hidden')));
setSidebarHidden(localStorage.getItem('sidebar-hidden') === '1');

async function loadSymbols() {
  const list = document.getElementById('symbol-list');
  let symbols;
  try {
    const res = await fetch('/api/symbols');
    if (!res.ok) throw new Error(res.statusText);
    symbols = await res.json();
  } catch {
    // README 式空态：失败要与「暂无自选/空列表」区分开（自 7d8111dd 意图移植，ADR-0005 合并）
    list.innerHTML = '<div class="sidebar-watch-empty">标的清单加载失败 · 确认 /api/symbols 已导出或服务已启动后刷新</div>';
    return;
  }
  list.innerHTML = '';

  const indices = symbols.filter(s => s.type === 'index' || ETF_LIKE.has(s.name));
  const stocks   = symbols.filter(s => s.type === 'stock' && !ETF_LIKE.has(s.name));

  // 自选（ADR-0005 T3：家住技术侧栏，与 symbol-list 同组同构；置顶分组，✕ 显式删除）
  const watchSet = new Set(loadWatch());
  const watchSyms = symbols.filter(s => watchSet.has(s.name));

  const buildRow = (s) => {
    const row = document.createElement('div');
    row.className = 'sidebar-row';
    row.dataset.name = s.name;
    if (s.name === currentSymbol) row.classList.add('active');
    const nameSpan = document.createElement('span');
    nameSpan.className = 'sidebar-row-name';
    nameSpan.textContent = s.name;
    row.appendChild(nameSpan);
    const priceSpan = document.createElement('span');
    priceSpan.className = 'sidebar-row-price';
    priceSpan.textContent = '--';
    row.appendChild(priceSpan);
    row.addEventListener('click', () => selectSymbol(s.name));
    return row;
  };

  const addSection = (title, items) => {
    const hdr = document.createElement('div');
    hdr.className = 'sidebar-section-header';
    hdr.textContent = title;
    list.appendChild(hdr);
    items.forEach(s => list.appendChild(buildRow(s)));
  };

  // 自选分组：置顶；头部带添加下拉（全部标的 - 已自选）；每行 ✕ 删除（≥24px 触控目标）
  const watchHdr = document.createElement('div');
  watchHdr.className = 'sidebar-section-header sidebar-watch-header';
  watchHdr.innerHTML = '<span>自选</span>';
  const addSel = document.createElement('select');
  addSel.className = 'sidebar-watch-add';
  addSel.setAttribute('aria-label', '添加自选标的');
  addSel.innerHTML = '<option value="">＋</option>'
    + symbols.filter(s => !watchSet.has(s.name)).map(s => `<option>${s.name}</option>`).join('');
  addSel.addEventListener('change', () => {
    if (!addSel.value) return;
    const next = loadWatch();
    next.push(addSel.value);
    saveWatchAndReload(next);
  });
  watchHdr.appendChild(addSel);
  list.appendChild(watchHdr);
  if (watchSyms.length) {
    watchSyms.forEach(s => {
      const row = buildRow(s);
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'watch-del';
      del.textContent = '✕';
      del.title = '删除自选';
      del.setAttribute('aria-label', `删除自选 ${s.name}`);
      del.addEventListener('click', e => {
        e.stopPropagation();
        removeWatch(s.name);
        loadSymbols();
      });
      row.appendChild(del);
      list.appendChild(row);
    });
  } else {
    const empty = document.createElement('div');
    empty.className = 'sidebar-watch-empty';
    empty.textContent = '暂无自选 · 用上方 ＋ 添加';
    list.appendChild(empty);
  }

  addSection('指数 & ETF', indices);
  addSection('个股', stocks);

  // 默认选中第一个标的（优先 SPX / AAPL / 首项）
  if (!currentSymbol && symbols.length) {
    const def = symbols.find(s => s.name === 'SPX') || symbols.find(s => s.name === 'AAPL') || symbols[0];
    selectSymbol(def.name);
  }

  fetchSidebarPrices(symbols);
}

// 添加后写回并重渲侧栏（统一入口，避免每处 saveWatch 后各自补渲染）
function saveWatchAndReload(next) {
  saveWatch(next);
  loadSymbols();
}

async function fetchSidebarPrices(symbols) {
  const results = await Promise.allSettled(
    symbols.map(s => fetch(`/api/kline/${s.name}?days=2`).then(r => r.json()))
  );
  symbols.forEach((s, i) => {
    const priceEls = document.querySelectorAll(`.sidebar-row[data-name="${s.name}"] .sidebar-row-price`);
    if (!priceEls.length) return;
    const r = results[i];
    if (r.status === 'rejected' || !Array.isArray(r.value) || r.value.length < 2) {
      priceEls.forEach(el => { el.textContent = '--'; });
      return;
    }
    const data = r.value;
    const last = data[data.length - 1];
    const prev = data[data.length - 2];
    const chg = ((last.close - prev.close) / prev.close) * 100;
    const up = chg >= 0;
    const html = `${last.close.toFixed(2)} <span class="${up ? 'up' : 'down'}">${up ? '+' : ''}${chg.toFixed(1)}%</span>`;
    priceEls.forEach(el => { el.innerHTML = html; });
  });
}

export async function selectSymbol(name) {
  currentSymbol = name;
  document.querySelectorAll('.sidebar-row').forEach(el => {
    el.classList.toggle('active', el.dataset.name === name);
  });
  document.querySelectorAll('.tech-ov-row').forEach(el => {
    el.classList.toggle('active', el.dataset.symbol === name);
  });
  document.getElementById('status-symbol').textContent = name;

  // 若总览已缓存该标的，立即先渲染深度结论卡（零等待）
  const cached = overviewList?.find(d => d.symbol === name);
  if (cached) {
    currentDiag = cached;
    renderCurrentConclusion(cached);
    renderDiag(cached);
  }

  fetchDiag();

  try {
    const res = await fetch(`/api/kline/${name}`);
    klineData = await res.json();
    applyRangeFilter();
    updateInfoBar();
    renderCharts();
  } catch {
    updateInfoBar();
  }
}

// ── 结论总览：全标的扫描 + 当前标的深度结论卡 ──────────────────────────────
async function loadTechOverview() {
  const scanEl = document.getElementById('tech-ov-scan');
  if (scanEl) scanEl.innerHTML = '<div class="tech-ov-card"><div class="loading">正在扫描全市场技术面信号…</div></div>';
  try {
    const data = await R.getOpt('/api/tech/overview');
    if (!Array.isArray(data) || !data.length) throw new Error('empty overview');
    overviewList = data;
    renderScanSection();
    if (currentSymbol && !currentDiag) {
      const hit = overviewList.find(d => d.symbol === currentSymbol);
      if (hit) {
        currentDiag = hit;
        renderCurrentConclusion(hit);
        renderDiag(hit);
        updateInfoBar();
      }
    }
  } catch {
    if (scanEl) {
      scanEl.innerHTML = '<div class="tech-ov-card"><div class="sidebar-watch-empty">全市场技术扫描暂不可用 · 请确认服务已启动（uv run python -m src.server）或已运行 uv run python -m src.export_pages</div></div>';
    }
  }
}

function renderCurrentConclusion(d) {
  const host = document.getElementById('tech-ov-current');
  if (!host || !d) return;

  const chg1d = d.changes?.['1d'];
  const chgHtml = chg1d != null ? R.chgSpan(chg1d) : '';
  const asOfText = R.asOf(d.last_date || '');

  const lines = Array.isArray(d.summary_lines) && d.summary_lines.length
    ? d.summary_lines
    : [d.summary_text || '暂无研判结论'];
  const lineLabels = R.LINES_CONCLUSION;

  const scoreBreak = (title, total, items) => `
    <div class="tech-ov-dim-col">
      <div class="tech-ov-dim-head">
        <span>${esc(title)}</span>
        <span class="diag-value ${total > 0 ? 'up' : total < 0 ? 'down' : 'neutral'}">${fmtSigned(total)}</span>
      </div>
      ${(items || []).map(s => `
        <div class="diag-row">
          <span class="diag-label">${esc(s.label)}</span>
          <span class="diag-value ${s.value > 0 ? 'up' : s.value < 0 ? 'down' : 'neutral'}">${fmtSigned(s.value)}</span>
        </div>
      `).join('') || '<div class="sidebar-watch-empty">无触发项</div>'}
    </div>
  `;

  host.innerHTML = '';

  // 1) 四张核心 KPI 卡作为顶层独立卡行（不塞进 .tech-ov-card，避免卡中卡双层框）
  const kpis = R.cards([
    {
      label: '综合评分 / 状态',
      value: `${fmtSigned(d.total_score)} · ${esc(d.regime_label || '—')}`,
      sub: `趋势 ${fmtSigned(d.trend_score)} · 动量 ${fmtSigned(d.momentum_score)} · 位置 ${fmtSigned(d.loc_score)}`,
      vcolor: scoreTokens(d.total_score).text,
      cls: 're-card-dense',
    },
    {
      label: '核心形态 / 信号',
      value: esc(d.setup_tag || d.regime_label || '—'),
      sub: `ADX ${d.ADX?.toFixed(1) ?? '—'} · RSI ${d.RSI?.toFixed(1) ?? '—'} · ${d.MACD_status === 'golden_cross' ? 'MACD金叉' : 'MACD死叉'}`,
      cls: 're-card-dense re-card-wrap',
    },
    {
      label: '关键支撑 / 阻力 (90D)',
      value: `$${d.support_90d?.toFixed(2) ?? '—'} — $${d.resistance_90d?.toFixed(2) ?? '—'}`,
      sub: `MA50 $${d.ma_values?.MA50?.toFixed(2) ?? '—'} (${fmtSigned(d.ma_dists?.MA50, 1, '%')}) · MA200 $${d.ma_values?.MA200?.toFixed(2) ?? '—'} (${fmtSigned(d.ma_dists?.MA200, 1, '%')})`,
      cls: 're-card-dense',
    },
    {
      label: '52 周区间分位 / 波幅',
      value: d.pos_52w != null ? `${d.pos_52w.toFixed(1)}%` : '—',
      sub: `52W $${d.low_52w?.toFixed(2) ?? '—'}–$${d.high_52w?.toFixed(2) ?? '—'} · ATR ${d.ATR_pct != null ? d.ATR_pct.toFixed(1) + '%' : '—'}${d.BB_width != null ? ` · 带宽 ${d.BB_width}%` : ''}`,
      cls: 're-card-dense',
    },
  ]);
  host.appendChild(kpis);

  // 2) 深度结论与三维依据明细卡
  const card = document.createElement('div');
  card.className = 'tech-ov-card';

  const head = document.createElement('div');
  head.className = 'tech-ov-head';
  head.innerHTML = `
    <div class="tech-ov-title-wrap">
      <span class="tech-ov-sym">${esc(d.symbol)}</span>
      <span class="tech-ov-price">${d.last_price != null ? '$' + d.last_price.toFixed(2) : '—'}</span>
      ${chgHtml}
      <span class="${regimeBadgeCls(d.regime)}">${esc(d.regime_label || '—')}</span>
      ${d.setup_tag && d.setup_tag !== d.regime_label ? `<span class="tag tech-ov-tag-setup">${esc(d.setup_tag)}</span>` : ''}
      ${d.BB_squeeze ? '<span class="tag tech-ov-tag-warn">Squeeze 压缩</span>' : ''}
      <span class="tech-ov-date">${esc(asOfText)}</span>
    </div>
    <button type="button" class="range-btn" id="tech-ov-go-kline">查看 K 线与指标图 →</button>
  `;
  head.querySelector('#tech-ov-go-kline')?.addEventListener('click', () => applySubview('kline', true));
  card.appendChild(head);

  // 三行结论（结论 / 依据 / 触发）
  const summaryBox = document.createElement('div');
  summaryBox.className = 'tech-ov-summary';
  summaryBox.innerHTML = lines.map((ln, idx) => `
    <div class="tech-ov-summary-item">
      <div class="tech-ov-summary-label">${esc(lineLabels[idx] || '要点')}</div>
      <p class="tech-ov-summary-text">${esc(ln)}</p>
    </div>
  `).join('');
  card.appendChild(summaryBox);

  // 三维评分依据明细（趋势 / 动量 / 位置与反转）
  const dimGrid = document.createElement('div');
  dimGrid.className = 'tech-ov-dim-grid';
  dimGrid.innerHTML =
    scoreBreak('趋势维度 (Trend)', d.trend_score, d.trend_scores) +
    scoreBreak('动量维度 (Momentum)', d.momentum_score, d.momentum_scores) +
    scoreBreak('位置与反转 (Location)', d.loc_score, d.loc_scores);
  card.appendChild(dimGrid);

  card.appendChild(R.secNote('评分口径：趋势（MA20/50/200 排列、MA200 牛熊线、ADX、SuperTrend、周/日 SMC 结构）+ 动量（MACD 金死叉与柱体、RSI 动量带、OBV 背离）+ 位置反转（RSI/Stoch 去重超买超卖、MFI 背离、50 日折溢价区、流动性扫荡、关键 K 线形态）。点击下方任意标的切换结论，双击或点行末「K线」直达图表。'));

  host.appendChild(card);
}

function filterOverviewItems(items) {
  if (!Array.isArray(items)) return [];
  const pred = SCAN_PREDICATES[activeFilter] || SCAN_PREDICATES.all;
  return items.filter(pred);
}

function renderScanSection() {
  const host = document.getElementById('tech-ov-scan');
  if (!host || !Array.isArray(overviewList)) return;

  const counts = Object.fromEntries(
    Object.entries(SCAN_PREDICATES).map(([k, fn]) => [k, overviewList.filter(fn).length])
  );

  const filters = [
    { key: 'all', label: `全部 (${counts.all})` },
    { key: 'bullish', label: `多头/偏多 (${counts.bullish})` },
    { key: 'bearish', label: `空头/偏空 (${counts.bearish})` },
    { key: 'squeeze', label: `Squeeze压缩 (${counts.squeeze})` },
    { key: 'oversold', label: `超卖/折价区 (${counts.oversold})` },
    { key: 'overbought', label: `超买/溢价区 (${counts.overbought})` },
  ];

  // 按综合分降序排列，便于一眼扫出最强与最弱
  const rows = [...filterOverviewItems(overviewList)].sort((a, b) => (b.total_score ?? 0) - (a.total_score ?? 0));

  host.innerHTML = '';
  const card = document.createElement('div');
  card.className = 'tech-ov-card';

  const toolbar = document.createElement('div');
  toolbar.className = 'tech-ov-scan-bar';
  toolbar.innerHTML = `
    <div class="tech-ov-scan-title">全标的信号扫描 <span class="tech-ov-date">共 ${overviewList.length} 标的 · 点击行查看结论，点「K线」切图表</span></div>
    <div class="tech-ov-filters">
      ${filters.map(f => `<button type="button" class="range-btn${activeFilter === f.key ? ' active' : ''}" data-filter="${f.key}">${esc(f.label)}</button>`).join('')}
    </div>
  `;
  toolbar.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      activeFilter = btn.dataset.filter;
      renderScanSection();
    });
  });
  card.appendChild(toolbar);

  const wrap = document.createElement('div');
  wrap.className = 'tech-ov-table-wrap';
  wrap.innerHTML = `
    <table class="tech-ov-table">
      <thead>
        <tr>
          <th scope="col">标的</th>
          <th scope="col" class="num">最新价</th>
          <th scope="col" class="num">1D 涨跌</th>
          <th scope="col">趋势状态</th>
          <th scope="col">核心形态 / Setup</th>
          <th scope="col" class="num">综合分</th>
          <th scope="col" class="num">趋势/动量/位置</th>
          <th scope="col" class="num">RSI(14)</th>
          <th scope="col" class="num">距 MA200</th>
          <th scope="col" class="num">52W 分位</th>
          <th scope="col" class="num">操作</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(d => {
          const chg1d = d.changes?.['1d'];
          const dist200 = d.ma_dists?.MA200;
          const isAct = d.symbol === currentSymbol;
          return `
            <tr class="tech-ov-row${isAct ? ' active' : ''}" data-symbol="${esc(d.symbol)}">
              <td><strong>${esc(d.symbol)}</strong> <span class="tech-ov-type">${d.type === 'index' || ETF_LIKE.has(d.symbol) ? '指数' : ''}</span></td>
              <td class="num">${d.last_price != null ? '$' + d.last_price.toFixed(2) : '—'}</td>
              <td class="num ${chg1d != null ? (chg1d > 0 ? 'up' : chg1d < 0 ? 'down' : 'neutral') : ''}">${chg1d != null ? fmtSigned(chg1d, 2, '%') : '—'}</td>
              <td><span class="${regimeBadgeCls(d.regime)}">${esc(d.regime_label || '—')}</span></td>
              <td>
                <span class="tag tech-ov-tag-setup">${esc(d.setup_tag || '—')}</span>
                ${d.BB_squeeze && d.setup_tag !== '波动率压缩(Squeeze)' ? '<span class="tag tech-ov-tag-warn">Squeeze</span>' : ''}
              </td>
              <td class="num"><strong class="${d.total_score > 0 ? 'up' : d.total_score < 0 ? 'down' : 'neutral'}">${fmtSigned(d.total_score)}</strong></td>
              <td class="num tech-ov-tri">${fmtSigned(d.trend_score)} / ${fmtSigned(d.momentum_score)} / ${fmtSigned(d.loc_score)}</td>
              <td class="num ${rsiToneCls(d.RSI)}">${d.RSI != null ? d.RSI.toFixed(1) : '—'}</td>
              <td class="num ${dist200 != null ? (dist200 > 0 ? 'up' : dist200 < 0 ? 'down' : '') : ''}">${dist200 != null ? fmtSigned(dist200, 1, '%') : '—'}</td>
              <td class="num">${d.pos_52w != null ? d.pos_52w.toFixed(0) + '%' : '—'}</td>
              <td class="num"><button type="button" class="range-btn tech-ov-kbtn" data-go-kline="${esc(d.symbol)}">K线</button></td>
            </tr>
          `;
        }).join('') || '<tr><td colspan="11" class="sidebar-watch-empty">当前筛选条件下无匹配标的</td></tr>'}
      </tbody>
    </table>
  `;

  wrap.querySelectorAll('.tech-ov-row').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('[data-go-kline]')) return;
      const sym = tr.dataset.symbol;
      if (sym) selectSymbol(sym);
    });
    tr.addEventListener('dblclick', () => {
      const sym = tr.dataset.symbol;
      if (sym) {
        selectSymbol(sym);
        applySubview('kline', true);
      }
    });
  });

  wrap.querySelectorAll('[data-go-kline]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sym = btn.dataset.goKline;
      if (sym) {
        selectSymbol(sym);
        applySubview('kline', true);
      }
    });
  });

  card.appendChild(wrap);
  host.appendChild(card);
}

// ── range filter ───────────────────────────────────────────────────────────
function applyRangeFilter() {
  if (!klineData || !klineData.length) { filteredData = klineData; return; }
  if (currentDays === 0) {
    filteredData = klineData;
  } else {
    filteredData = klineData.slice(-currentDays);
  }
  document.getElementById('status-count').textContent = `${filteredData.length} 条数据`;
  if (filteredData.length > 0) {
    document.getElementById('status-range').textContent =
      `${filteredData[0].date} → ${filteredData[filteredData.length - 1].date}`;
  }
}

function initRangeControls() {
  document.querySelectorAll('#kline-range-controls .range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#kline-range-controls .range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentDays = Number(btn.dataset.range);
      applyRangeFilter();
      updateInfoBar();
      renderCharts();
    });
  });
}

// ── charts ─────────────────────────────────────────────────────────────────
let _chartContainers = []; // ResizeObserver targets

function initCharts() {
  const mainEl   = document.getElementById('main-chart');
  const rsiEl    = document.getElementById('rsi-chart');
  const macdEl   = document.getElementById('macd-chart');

  mainChart = LightweightCharts.createChart(mainEl, { ...CHART_OPTS, width: mainEl.clientWidth || 600, height: mainEl.clientHeight || 260 });
  mainChart.priceScale('right').applyOptions({ autoScale: true, scaleMargins: { top: 0.1, bottom: 0.15 } });
  candleSeries = mainChart.addCandlestickSeries(candleColors());
  // ponytail: thin volume overlay on main chart for quick visual reference
  volumeOverlaySeries = mainChart.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: 'vol',
    color: volumeColor(),
  });
  mainChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: true });

  rsiChart = LightweightCharts.createChart(rsiEl, { ...CHART_OPTS, width: rsiEl.clientWidth || 600, height: rsiEl.clientHeight || 120 });
  rsiChart.priceScale('right').applyOptions({ minimum: 0, maximum: 100 });
  rsiSeries = rsiChart.addLineSeries({ color: cssVar('--chart-violet'), lineWidth: 1.5 });
  rsiUpper = rsiChart.addLineSeries({ color: cssVar('--color-down'), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  rsiLower = rsiChart.addLineSeries({ color: cssVar('--color-up'), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });

  macdChart = LightweightCharts.createChart(macdEl, { ...CHART_OPTS, width: macdEl.clientWidth || 600, height: macdEl.clientHeight || 120 });
  macdHistSeries = macdChart.addHistogramSeries({ priceFormat: { type: 'price', precision: 2 } });
  macdLineSeries = macdChart.addLineSeries({ color: cssVar('--color-brand'), lineWidth: 1.5 });
  macdSignalSeries = macdChart.addLineSeries({ color: cssVar('--color-warn'), lineWidth: 1.5 });

  // resize observer — auto-adapt when container size changes (panel toggle, window resize)
  _chartContainers.forEach(ro => ro?.disconnect());
  _chartContainers = [];
  const charts = [
    { el: mainEl, chart: mainChart },
    { el: rsiEl, chart: rsiChart },
    { el: macdEl, chart: macdChart },
  ];
  charts.forEach(({ el, chart }) => {
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        const w = e.contentRect.width;
        const h = e.contentRect.height;
        if (w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
      }
    });
    ro.observe(el);
    _chartContainers.push(ro);
  });

  // 主题热切换：布局 + 各 series 颜色都重新从当前主题 token 取，再重绘（renderCharts 内含 overlay/逐点色重算）
  window.addEventListener('theme-changed', () => {
    [mainChart, rsiChart, macdChart].forEach(c => c.applyOptions(darkChartOpts()));
    applySeriesColors();
    renderCharts();
  });
  // 应用初始主题
  if (document.body.classList.contains('dark')) {
    const darkOpts = darkChartOpts();
    [mainChart, rsiChart, macdChart].forEach(c => c.applyOptions(darkOpts));
  }

  // sync time scales across all 3 charts
  const allCharts = [mainChart, rsiChart, macdChart];
  const syncFrom = (src, targets) => {
    src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      targets.forEach(t => t.timeScale().setVisibleLogicalRange(range));
    });
  };
  allCharts.forEach(src => syncFrom(src, allCharts.filter(t => t !== src)));

  // crosshair sync → diag
  // 副图十字光标同步：lightweight-charts v4.2.2 的签名是
  // setCrosshairPosition(price, horizontalPosition: time, seriesApi)，第三参数必须是
  // seriesApi 句柄（内部 xw.get(seriesApi) 查不到就静默 return）。
  // 原实现传 (NaN, NaN, param.time) 从未生效，这里改为 [图, 该图的 seriesApi, 数据列名]。
  const subCharts = [[rsiChart, rsiSeries, 'RSI'], [macdChart, macdHistSeries, 'MACD_hist']];
  mainChart.subscribeCrosshairMove(param => {
    if (!param || !param.time) {
      crosshairDate = null;
      subCharts.forEach(([c]) => c.clearCrosshairPosition());
      return;
    }
    crosshairDate = param.time;
    const row = filteredData ? filteredData.find(d => d.date === param.time) : null;
    subCharts.forEach(([c, s, key]) => {
      const v = row ? row[key] : null;
      if (v == null) c.clearCrosshairPosition();
      else c.setCrosshairPosition(v, param.time, s);
    });
    debouncedDiag();
  });
}

// ── 图表颜色：单源 tokens.css ───────────────────────────────────────────────
function candleColors() {
  const up = cssVar('--color-up'), down = cssVar('--color-down');
  return {
    upColor: up, downColor: down, borderUpColor: up, borderDownColor: down,
    wickUpColor: up, wickDownColor: down,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  };
}
// 量能柱：品牌蓝半透明（8 位十六进制后缀，承接原浅蓝量能色的语义位）
function volumeColor() { return cssVar('--color-brand') + '99'; }

// 主题切换后把新色应用到非逐点着色的 series（逐点色由 renderCharts/refreshOverlays 重算）
function applySeriesColors() {
  candleSeries.applyOptions(candleColors());
  volumeOverlaySeries.applyOptions({ color: volumeColor() });
  rsiSeries.applyOptions({ color: cssVar('--chart-violet') });
  rsiUpper.applyOptions({ color: cssVar('--color-down') });
  rsiLower.applyOptions({ color: cssVar('--color-up') });
  macdLineSeries.applyOptions({ color: cssVar('--color-brand') });
  macdSignalSeries.applyOptions({ color: cssVar('--color-warn') });
}

function renderCharts() {
  const data = filteredData;
  if (!data || !data.length) return;
  const candles = data.map(d => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close }));
  const volumes = data.map(d => ({
    time: d.date,
    value: d.volume ?? 0,
    color: (d.close >= d.open ? cssVar('--color-up') : cssVar('--color-down')) + '4D',
  }));
  candleSeries.setData(candles);
  volumeOverlaySeries.setData(volumes);

  // RSI
  const rsi = data.filter(d => d.RSI != null).map(d => ({ time: d.date, value: d.RSI }));
  rsiSeries.setData(rsi);
  if (rsi.length) {
    const times = rsi.map(d => d.time);
    rsiUpper.setData(times.map(t => ({ time: t, value: 70 })));
    rsiLower.setData(times.map(t => ({ time: t, value: 30 })));
  }

  // MACD
  const hist = data.filter(d => d.MACD_hist != null).map(d => ({
    time: d.date, value: d.MACD_hist,
    color: d.MACD_hist >= 0 ? cssVar('--color-up') : cssVar('--color-down'),
  }));
  const macdLine = data.filter(d => d.MACD != null).map(d => ({ time: d.date, value: d.MACD }));
  const signalLine = data.filter(d => d.MACD_signal != null).map(d => ({ time: d.date, value: d.MACD_signal }));
  macdHistSeries.setData(hist);
  macdLineSeries.setData(macdLine);
  macdSignalSeries.setData(signalLine);

  refreshOverlays();
  mainChart.timeScale().fitContent();
}

// ── overlays ───────────────────────────────────────────────────────────────
function initControls() {
  document.querySelectorAll('#controls input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', refreshOverlays);
  });
  initRangeControls();
}

function refreshOverlays() {
  const data = filteredData;
  if (!data) return;
  Object.values(maSeries).forEach(s => { try { mainChart.removeSeries(s); } catch {} });
  maSeries = {};

  const active = [...document.querySelectorAll('#controls input[type="checkbox"]:checked')].map(c => c.dataset.ma);

  active.forEach(key => {
    if (key === 'BB') {
      const bb = BB_COLOR();
      const upper = data.filter(d => d.BB_upper != null).map(d => ({ time: d.date, value: d.BB_upper }));
      const lower = data.filter(d => d.BB_lower != null).map(d => ({ time: d.date, value: d.BB_lower }));
      const mid = data.filter(d => d.BB_mid != null).map(d => ({ time: d.date, value: d.BB_mid }));
      maSeries['BB_upper'] = addLine(mainChart, upper, bb, 1, 2);
      maSeries['BB_lower'] = addLine(mainChart, lower, bb, 1, 2);
      maSeries['BB_mid'] = addLine(mainChart, mid, bb, 1, 0);
    } else if (key === 'ST') {
      const stUp = data.filter(d => d.SUPERT_dir === 1 && d.SUPERT != null).map(d => ({ time: d.date, value: d.SUPERT }));
      const stDn = data.filter(d => d.SUPERT_dir === -1 && d.SUPERT != null).map(d => ({ time: d.date, value: d.SUPERT }));
      maSeries['ST_up'] = addLine(mainChart, stUp, cssVar('--color-up'), 2, 0);
      maSeries['ST_dn'] = addLine(mainChart, stDn, cssVar('--color-down'), 2, 0);
    } else {
      const d2 = data.filter(d => d[key] != null).map(d => ({ time: d.date, value: d[key] }));
      const width = key === 'MA200' ? 2 : 1.5;
      maSeries[key] = addLine(mainChart, d2, MA_COLORS()[key] || cssVar('--text-secondary'), width, 0);
    }
  });
}

// ── diagnosis ──────────────────────────────────────────────────────────────
function debouncedDiag() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchDiag, 200);
}

async function fetchDiag() {
  if (!currentSymbol) return;
  // Pages 静态部署：as_of 光标回看无法预渲染所有日期，固定返回最新诊断
  const url = `/api/diag/${currentSymbol}`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    const d = await res.json();
    currentDiag = d;
    renderDiag(d);
    renderCurrentConclusion(d);
  } catch {
    if (!currentDiag) {
      document.getElementById('diag-content').innerHTML = `<div class="loading">诊断加载失败 —— 请确认服务已启动（uv run python -m src.server）</div>`;
    }
  }
}

function renderDiag(d) {
  const el = document.getElementById('diag-content');
  if (!el || !d) return;
  const maxScore = 18;
  const pct = Math.max(0, Math.min(100, ((d.total_score + maxScore) / (2 * maxScore)) * 100));
  const { text: scoreTxtTok, fill: scoreFillTok } = scoreTokens(d.total_score);

  let html = `
    <div class="diag-section">
      <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">
        <span class="score-text" style="color:${scoreTxtTok}">${fmtSigned(d.total_score)}</span>
        <span class="${regimeBadgeCls(d.regime)}">${esc(d.regime_label || '—')}</span>
        <span style="color:var(--text-secondary);font-size:12px;margin-left:auto">${esc(d.last_date || '')}</span>
      </div>
      <div class="score-bar"><div class="score-fill" style="width:${pct}%;background:${scoreFillTok}"></div></div>
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:18px;font-weight:600">${d.last_price != null ? '$' + d.last_price.toFixed(2) : '—'}</span>
        <span style="font-size:12px;color:var(--text-secondary)">趋势 ${fmtSigned(d.trend_score)} · 动量 ${fmtSigned(d.momentum_score)} · 位置 ${fmtSigned(d.loc_score)}</span>
      </div>
    </div>

    <div class="diag-section">
      <h4>均线 & 偏离度</h4>
      ${Object.entries(d.ma_signals || {}).map(([k, v]) => {
        const dist = d.ma_dists?.[k];
        return `
        <div class="diag-row">
          <span class="diag-label">${esc(k)}</span>
          <span class="diag-value">
            ${(d.ma_values?.[k] ?? '—')}
            <span class="${v === 'above' ? 'up' : 'down'}">${v === 'above' ? '↑' : '↓'}${dist != null ? ` ${fmtSigned(dist, 1, '%')}` : ''}</span>
          </span>
        </div>`;
      }).join('')}
    </div>

    <div class="diag-section">
      <h4>RSI / 布林带</h4>
      <div class="diag-row">
        <span class="diag-label">RSI(14)</span>
        <span class="diag-value ${rsiToneCls(d.RSI, 'neutral')}">${d.RSI?.toFixed(1) ?? '—'} (${esc(d.RSI_detail || '')})</span>
      </div>
      <div class="diag-row"><span class="diag-label">BB 位置 / 带宽</span><span class="diag-value">${d.BB_position != null ? d.BB_position.toFixed(0) + '%' : '—'} · ${d.BB_width != null ? d.BB_width + '%' : '—'}${d.BB_squeeze ? ' (Squeeze)' : ''}</span></div>
    </div>

    <div class="diag-section">
      <h4>MACD / ADX</h4>
      <div class="diag-row">
        <span class="diag-label">MACD 状态</span>
        <span class="diag-value ${d.MACD_status === 'golden_cross' ? 'up' : 'down'}">
          ${d.MACD_status === 'golden_cross' ? '✦ 金叉' : '✦ 死叉'} (${d.MACD_hist?.toFixed(3) ?? '—'})
        </span>
      </div>
      <div class="diag-row"><span class="diag-label">ADX / ±DI</span><span class="diag-value">${d.ADX?.toFixed(1) ?? '—'} (<span class="up">+${d.DMP?.toFixed(0) ?? '—'}</span> / <span class="down">-${d.DMN?.toFixed(0) ?? '—'}</span>)</span></div>
    </div>

    <div class="diag-section">
      <h4>关键位 & 52周</h4>
      <div class="diag-row"><span class="diag-label">90d 阻力 / 支撑</span><span class="diag-value"><span class="down">${d.resistance_90d?.toFixed(2) ?? '—'}</span> / <span class="up">${d.support_90d?.toFixed(2) ?? '—'}</span></span></div>
      <div class="diag-row"><span class="diag-label">52w 区间 (${d.pos_52w != null ? d.pos_52w.toFixed(0) + '%' : '—'})</span><span class="diag-value">${d.low_52w?.toFixed(2) ?? '—'} – ${d.high_52w?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">ATR / 量比</span><span class="diag-value">${d.ATR?.toFixed(2) ?? '—'} (${d.ATR_pct ?? '—'}%) · ${d.vol_ratio?.toFixed(2) ?? '—'}x</span></div>
    </div>

    <div class="diag-section">
      <h4>其它指标（CCI 仅展示不计分）</h4>
      <div class="diag-row"><span class="diag-label">Stoch K/D</span><span class="diag-value">${d.STOCH_k?.toFixed(1) ?? '—'} / ${d.STOCH_d?.toFixed(1) ?? '—'} (${esc(d.STOCH_detail || '—')})</span></div>
      <div class="diag-row"><span class="diag-label">CCI</span><span class="diag-value">${d.CCI?.toFixed(1) ?? '—'} (${esc(d.CCI_detail || '—')})</span></div>
      <div class="diag-row"><span class="diag-label">MFI</span><span class="diag-value">${d.MFI?.toFixed(1) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">OBV 背离</span><span class="diag-value">${esc(d.OBV_divergence || '无')}</span></div>
    </div>

    <div class="diag-section">
      <h4>阶段涨跌幅</h4>
      ${Object.entries(d.changes || {}).map(([k, v]) => `
        <div class="diag-row">
          <span class="diag-label">${esc(k)}</span>
          <span class="diag-value ${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%</span>
        </div>
      `).join('')}
    </div>

    <div class="diag-section">
      <h4>K线形态 (已滤除高频噪声)</h4>
      <div class="cdl-list">
        ${(d.cdl_bullish || []).map(n => `<span class="tag tag-bull">${esc(n)}</span>`).join('')}
        ${(d.cdl_bearish || []).map(n => `<span class="tag tag-bear">${esc(n)}</span>`).join('')}
        ${!(d.cdl_bullish?.length || d.cdl_bearish?.length) ? '<span style="color:var(--text-dim);font-size:12px">无显著反转形态</span>' : ''}
      </div>
    </div>

    <div class="diag-section">
      <h4>评分明细</h4>
      ${(d.scores || []).map(s => `
        <div class="diag-row">
          <span class="diag-label">${esc(s.label)}</span>
          <span class="diag-value ${s.value > 0 ? 'up' : s.value < 0 ? 'down' : 'neutral'}">${fmtSigned(s.value)}</span>
        </div>
      `).join('')}
    </div>
  `;
  el.innerHTML = html;
}

// ── keyboard ───────────────────────────────────────────────────────────────
// 方向键回看的两道守卫：
// 1) 视图状态源 = app.js switchTab() 写入的 .app[data-view]（CSS 也靠它切换视图），
//    离开技术视图（仪表盘/宏观/关联）或不在 K 线子视图时不接管，否则页面滚动会被吞。
// 2) 焦点在可输入元素上（含 contenteditable / [role=combobox]）不接管，
//    否则搜索框、代码输入、select 里的方向键全部失效。
const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
function isEditableTarget(el) {
  if (!el || typeof el !== 'object') return false;
  if (EDITABLE_TAGS.has(el.tagName)) return true;
  if (el.isContentEditable) return true;
  return typeof el.getAttribute === 'function' && el.getAttribute('role') === 'combobox';
}

function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (app.dataset.view !== 'tech' || currentSubview !== 'kline') return;
    if (isEditableTarget(e.target) || isEditableTarget(document.activeElement)) return;
    const data = filteredData;
    if (!data || !data.length) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const ts = mainChart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (!range) return;
      const totalBars = data.length;
      let idx = crosshairDate ? data.findIndex(d => d.date === crosshairDate) : Math.floor(totalBars / 2);
      idx += e.key === 'ArrowRight' ? 1 : -1;
      idx = Math.max(0, Math.min(totalBars - 1, idx));
      crosshairDate = data[idx].date;
      // horizontalPosition 是 time（不是 bar index）：传 idx 会被当成 1970 年的时间戳，光标恒停在首根
      mainChart.setCrosshairPosition(data[idx].close, data[idx].date, candleSeries);
      debouncedDiag();
    }
  });
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = currentSymbol || '—';
  const data = filteredData || klineData;
  if (data && data.length) {
    document.getElementById('status-count').textContent = `${data.length} 条数据`;
    document.getElementById('status-range').textContent =
      `${data[0].date} → ${data[data.length - 1].date}`;
  } else {
    document.getElementById('status-count').textContent = '';
    document.getElementById('status-range').textContent = '';
  }
}
