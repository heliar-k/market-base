import { registerMacroTheme, reThemeECharts } from './echarts-theme.js';

// ponytail: default watchlist, CRUD later
const WATCHLIST = ['AAPL', 'NVDA', 'SPY', 'QQQ', 'TSLA'];

let data = {};
let miniCharts = [];

// ── public API ──────────────────────────────────────────────
export async function initDashboard() {
  registerMacroTheme();
  window.addEventListener('theme-changed', onDashThemeChanged);
  const root = document.querySelector('.dashboard-view');
  root.classList.remove('placeholder');
  root.innerHTML = '';
  data = {};
  miniCharts = [];

  root.appendChild(buildTopRow());
  const grid = el('div', 'dash-grid');
  grid.appendChild(buildMacroOverview());
  grid.appendChild(buildWatchlist());
  grid.appendChild(buildLiquiditySnap());
  grid.appendChild(buildIndicesPanel());
  root.appendChild(grid);

  await refresh();
}

export function cleanup() {
  miniCharts.forEach(c => { try { c.dispose(); } catch (e) { /* ignore */ } });
  miniCharts = [];
  window.removeEventListener('theme-changed', onDashThemeChanged);
}

function onDashThemeChanged() {
  miniCharts = miniCharts.map(c => {
    const dom = c.getDom();
    if (!dom || !dom.isConnected) return c;
    const opts = c.getOption();
    return reThemeECharts(c, dom, opts);
  });
}

export function refresh() {
  return Promise.all([
    refreshCards(),
    refreshMiniCharts(),
    refreshWatchlist(),
    refreshLiquidity(),
    refreshIndices(),
  ]);
}

export function updateStatus() {
  document.getElementById('status-symbol').textContent = '仪表盘';
  document.getElementById('status-range').textContent = '市场概览';
  document.getElementById('status-count').textContent = '';
}

// ── DOM builders ────────────────────────────────────────────
function buildTopRow() {
  const row = el('div', 'dash-top');
  for (const id of ['VIX', 'CPI', 'FEDFUNDS', 'DXY']) {
    const card = el('div', 'dash-stat');
    card.id = 'ds-' + id;
    card.innerHTML = loadingHTML();
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => switchTo('macro'));
    row.appendChild(card);
  }
  return row;
}

function buildMacroOverview() {
  const card = el('div', 'dash-card');
  card.innerHTML = '<div class="dash-card-title">宏观概览</div>';
  const charts = el('div', 'dash-mini-row');
  const nWrap = el('div', 'dash-mini-wrap');
  nWrap.innerHTML = '<div class="dash-mini-label">净流动性 (NET_LIQ)</div>';
  const nDiv = el('div'); nDiv.id = 'mini-netliq'; nDiv.className = 'dash-mini-chart';
  nWrap.appendChild(nDiv);
  nWrap.style.cursor = 'pointer';
  nWrap.addEventListener('click', () => switchTo('liquidity'));

  const sWrap = el('div', 'dash-mini-wrap');
  sWrap.innerHTML = '<div class="dash-mini-label">期限利差 (2S10S)</div>';
  const sDiv = el('div'); sDiv.id = 'mini-spread'; sDiv.className = 'dash-mini-chart';
  sWrap.appendChild(sDiv);
  sWrap.style.cursor = 'pointer';
  sWrap.addEventListener('click', () => switchTo('correlation'));

  charts.append(nWrap, sWrap);
  card.appendChild(charts);
  return card;
}

function buildWatchlist() {
  const card = el('div', 'dash-card');
  card.innerHTML = '<div class="dash-card-title">自选股监控</div>';
  const body = el('div', 'dash-watchlist');
  body.id = 'dash-watchlist';
  body.innerHTML = loadingHTML();
  card.appendChild(body);
  return card;
}

function buildLiquiditySnap() {
  const card = el('div', 'dash-card');
  card.innerHTML = '<div class="dash-card-title">流动性快照</div>';
  const body = el('div', 'dash-liq-body');
  body.id = 'dash-liq-body';
  body.innerHTML = loadingHTML();
  card.appendChild(body);
  return card;
}

function buildIndicesPanel() {
  const card = el('div', 'dash-card');
  card.innerHTML = '<div class="dash-card-title">股指一览</div>';
  const body = el('div', 'dash-indices');
  body.id = 'dash-indices';
  body.innerHTML = loadingHTML();
  card.appendChild(body);
  return card;
}

// ── data loaders ────────────────────────────────────────────
async function refreshCards() {
  const [vix, cpi, fedFunds, fx] = await Promise.allSettled([
    fetchJSON('/api/macro/volatility'),
    fetchJSON('/api/macro/inflation'),
    fetchJSON('/api/rates/fed-funds'),
    fetchJSON('/api/macro/fx'),
  ]);
  data.vix = vix; data.cpi = cpi; data.rates = fedFunds; data.fx = fx;
  // fed-funds API 的 recent 30 天 effr 序列 → 伪 records 数组（喂给 renderStatCard）
  const ff = fedFunds.value;
  const rates = (fedFunds.status === 'fulfilled' && ff?.recent)
    ? { status: 'fulfilled', value: ff.recent.map(r => ({ date: r.date, DFF: r.effr })) }
    : fedFunds;
  renderStatCard('ds-VIX', vix, 'VIX', '波动率指数', 2);
  renderStatCard('ds-CPI', cpi, 'CPI', '消费者物价', 1, '%', 'yoy');
  renderStatCard('ds-FEDFUNDS', rates, 'DFF', '联邦基金利率', 2, '%');
  renderStatCard('ds-DXY', fx, 'DXY', '美元指数', 2);
}

async function refreshMiniCharts() {
  // ponytail: clear all mini charts once before re-rendering both
  miniCharts.forEach(c => { try { c.dispose(); } catch (e) { /* ignore */ } });
  miniCharts = [];
  // 迷你图数据源用轻量 API（rates 全量 16MB 只为画 2s10s 太浪费）
  const [liq, yc] = await Promise.allSettled([
    fetchJSON('/api/liquidity/overview?range=1y'),
    fetchJSON('/api/rates/yield-curve'),
  ]);
  renderMiniChart('mini-netliq', liq, 'NET_LIQUIDITY', '#26a69a');
  renderMiniChart('mini-spread', yc, '2s10s', '#ff9800');
}

async function refreshWatchlist() {
  const results = await Promise.allSettled(
    WATCHLIST.map(s => fetchJSON(`/api/kline/${s}?days=5`))
  );
  data.watchlist = results;
  renderWatchlist();
}

async function refreshLiquidity() {
  const res = await fetchJSON('/api/liquidity/overview?range=all');
  data.liqSummary = res?.summary ?? {};
  renderLiquidity();
}

async function refreshIndices() {
  const [spy, qqq, vix] = await Promise.allSettled([
    fetchJSON('/api/kline/SPY?days=5'),
    fetchJSON('/api/kline/QQQ?days=5'),
    fetchJSON('/api/macro/volatility'),
  ]);
  data.indices = { spy, qqq, vix };
  renderIndices();
}

// ── renderers ───────────────────────────────────────────────

// Top stat card — macro API returns flat array [{date, KEY, ...}, ...]
// mode: '1w' (default, absolute 1-week change) | 'yoy' (CPI: YoY % change)
function renderStatCard(id, result, key, cnLabel, precision = 2, suffix = '', mode = '1w') {
  const card = document.getElementById(id);
  if (!card) return;
  const errHTML = () => `<div class="dash-stat-value">--</div>
    <div class="dash-stat-label">${key}</div>
    <div class="dash-stat-desc">${cnLabel}</div>`;

  if (result.status !== 'fulfilled' || !Array.isArray(result.value)) {
    card.innerHTML = errHTML();
    return;
  }

  const values = result.value.filter(d => d[key] != null).map(d => d[key]);
  if (values.length < 2) { card.innerHTML = errHTML(); return; }

  const latest = values[values.length - 1];
  let displayVal, change, changeLabel;

  if (mode === 'yoy') {
    // CPI: show YoY percentage change, change shows MoM delta
    const yearAgo = values.length > 12 ? values[values.length - 13] : values[0];
    displayVal = yearAgo ? ((latest - yearAgo) / yearAgo * 100) : null;
    const prevMonth = values[values.length - 2];
    const prevYoY = values.length > 13
      ? ((prevMonth - values[values.length - 14]) / values[values.length - 14] * 100)
      : null;
    change = (displayVal != null && prevYoY != null) ? displayVal - prevYoY : 0;
    changeLabel = '1Mo';
  } else {
    displayVal = latest;
    const weekAgo = values[Math.max(0, values.length - 6)];
    change = latest - weekAgo;
    changeLabel = '1W';
  }

  if (displayVal == null) { card.innerHTML = errHTML(); return; }

  const arrow = change > 0.01 ? '↑' : change < -0.01 ? '↓' : '=';
  const cls = change > 0.01 ? 'up' : change < -0.01 ? 'down' : 'neutral';
  const sign = change > 0 ? '+' : '';

  card.innerHTML = `
    <div class="dash-stat-value">${fmtNum(displayVal, precision)}${suffix}</div>
    <div class="dash-stat-label">${key}</div>
    <div class="dash-stat-desc">${cnLabel}</div>
    <div class="dash-stat-change ${cls}">
      ${arrow} ${sign}${fmtNum(change, precision)} ${changeLabel}
    </div>`;
}

// Mini chart — ECharts sparkline (no axis labels, no interactivity)
async function renderMiniChart(containerId, result, key, color) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (result.status !== 'fulfilled') {
    container.innerHTML = '<div class="loading" style="height:120px">--</div>';
    return;
  }

  let seriesData = null;
  const val = result.value;
  if (val?.series?.[key]) {
    seriesData = val.series[key]
      .filter(d => d.value != null)
      .map(d => [d.date, d.value]);
  } else if (val?.yield_curve?.spreads_history?.[key]) {
    // yield-curve API：spreads_history 为 {date, value} 点数组（bp）
    seriesData = val.yield_curve.spreads_history[key]
      .filter(d => d.value != null)
      .map(d => [d.date, d.value]);
  } else if (Array.isArray(val)) {
    seriesData = val
      .filter(d => d[key] != null)
      .map(d => [d.date, d[key]]);
  }

  if (!seriesData || seriesData.length < 2) {
    container.innerHTML = '<div class="loading" style="height:120px">--</div>';
    return;
  }

  container.innerHTML = '';
  const chart = echarts.init(container, 'macro', { renderer: 'canvas' });
  miniCharts.push(chart);
  chart.setOption({
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: 'time', show: false, boundaryGap: false },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line', data: seriesData, lineStyle: { color, width: 1.5 },
      showSymbol: false, silent: true,
    }],
    tooltip: { show: false },
  });
  new ResizeObserver(() => chart.resize()).observe(container);
}

function renderWatchlist() {
  const body = document.getElementById('dash-watchlist');
  if (!body) return;
  if (!data.watchlist) { body.innerHTML = loadingHTML(); return; }
  let html = '';
  data.watchlist.forEach((r, i) => {
    const sym = WATCHLIST[i];
    // Kline API returns flat array: [{date, open, high, low, close, volume}, ...]
    if (r.status !== 'fulfilled' || !Array.isArray(r.value) || r.value.length < 2) {
      html += watchRow(sym, '--', null);
      return;
    }
    const arr = r.value;
    const price = arr[arr.length - 1].close;
    const prev = arr[arr.length - 2].close;
    const pct = prev ? ((price - prev) / prev * 100) : 0;
    html += watchRow(sym, fmtNum(price, 2), pct);
  });
  body.innerHTML = html;
  body.querySelectorAll('[data-go-stock]').forEach(row => {
    row.addEventListener('click', () => {
      const sym = row.dataset.goStock;
      window.dispatchEvent(new CustomEvent('go-stock', { detail: sym }));
    });
  });
}

function renderLiquidity() {
  const body = document.getElementById('dash-liq-body');
  if (!body) return;
  if (!data.liqSummary || !Object.keys(data.liqSummary).length) {
    body.innerHTML = loadingHTML();
    return;
  }
  const rows = [
    ['WALCL', '美联储总资产'],
    ['NET_LIQUIDITY', '净流动性'],
    ['RRPONTSYD', '隔夜逆回购'],
  ];
  body.innerHTML = rows.map(([key, label]) => {
    const info = data.liqSummary[key];
    const val = info?.latest_value != null ? fmtLiq(info.latest_value) : '--';
    return `<div class="dash-liq-row">
      <span class="dash-liq-label">${key} <small>${label}</small></span>
      <span class="dash-liq-value">${val}</span>
    </div>`;
  }).join('');
}

function renderIndices() {
  const body = document.getElementById('dash-indices');
  if (!body) return;
  if (!data.indices) { body.innerHTML = loadingHTML(); return; }
  const { spy, qqq, vix } = data.indices;

  // Kline returns flat array of OHLCV
  const klineRow = (label, result) => {
    if (result.status !== 'fulfilled' || !Array.isArray(result.value) || result.value.length < 2) {
      return `<div class="dash-idx-row"><span>${label}</span><span>--</span></div>`;
    }
    const arr = result.value;
    const price = arr[arr.length - 1].close;
    const prev = arr[arr.length - 2].close;
    const change = price - prev;
    const cls = change >= 0 ? 'up' : 'down';
    const sign = change >= 0 ? '+' : '';
    return `<div class="dash-idx-row">
      <span>${label}</span>
      <span>${fmtNum(price, 2)} <span class="${cls}">${sign}${fmtNum(change, 2)}</span></span>
    </div>`;
  };

  // VIX from macro volatility (flat array with VIX column)
  const vixRow = () => {
    if (vix.status !== 'fulfilled' || !Array.isArray(vix.value)) {
      return `<div class="dash-idx-row"><span>VIX</span><span>--</span></div>`;
    }
    const vals = vix.value.filter(d => d.VIX != null).map(d => d.VIX);
    if (vals.length < 2) {
      return `<div class="dash-idx-row"><span>VIX</span><span>--</span></div>`;
    }
    const latest = vals[vals.length - 1];
    const prev = vals[vals.length - 2];
    const change = latest - prev;
    const cls = change >= 0 ? 'up' : 'down';
    const sign = change >= 0 ? '+' : '';
    return `<div class="dash-idx-row">
      <span>VIX</span>
      <span>${fmtNum(latest, 2)} <span class="${cls}">${sign}${fmtNum(change, 2)}</span></span>
    </div>`;
  };

  body.innerHTML = klineRow('S&P 500 (SPY)', spy)
    + klineRow('Nasdaq (QQQ)', qqq)
    + vixRow();
}

// ── helpers ─────────────────────────────────────────────────
function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function loadingHTML() {
  return '<div class="loading">加载中…</div>';
}

function watchRow(sym, price, pct) {
  if (pct == null) {
    return `<div class="dash-watch-row" data-go-stock="${sym}">
      <span class="dash-watch-sym">${sym}</span><span class="dash-watch-price">${price}</span><span>--</span></div>`;
  }
  const cls = pct >= 0 ? 'up' : 'down';
  const icon = pct >= 0 ? '📈' : '📉';
  const sign = pct >= 0 ? '+' : '';
  return `<div class="dash-watch-row" data-go-stock="${sym}">
    <span class="dash-watch-sym">${sym}</span>
    <span class="dash-watch-price">${price}</span>
    <span class="${cls}">${sign}${pct.toFixed(2)}% ${icon}</span>
  </div>`;
}

function switchTo(tab) {
  window.dispatchEvent(new CustomEvent('switch-tab', { detail: tab }));
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

function fmtNum(n, p = 2) {
  if (n == null || isNaN(n)) return '--';
  return Number(n).toFixed(p);
}

// ponytail: values in millions from FRED; ≥10^6 = 万亿, else 亿
function fmtLiq(v) {
  if (v == null || isNaN(v)) return '--';
  if (Math.abs(v) >= 1000000) return (v / 1000000).toFixed(2) + '万亿';
  return Math.round(v) + '亿';
}
