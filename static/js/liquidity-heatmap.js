// liquidity-heatmap.js — liquidity overview with ECharts

import { registerMacroTheme, reThemeECharts } from './echarts-theme.js';
import { MACRO_DATE_RANGES } from './macro-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let overviewData = null;
let dateRange = 'all';
let charts = {};
let chartObservers = {};
let highlightedCard = null;

const COLORS = {
  WALCL: '#1a73e8', RRPONTSYD: '#ff9800', WRESBAL: '#26a69a',
  WTREGEN: '#ef5350', NET_LIQUIDITY: '#9c27b0', NFCI: '#607d8b',
};

const LABELS = {
  WALCL: '美联储总资产', RRPONTSYD: '隔夜逆回购', WRESBAL: '准备金余额',
  WTREGEN: 'TGA余额', NET_LIQUIDITY: '净流动性', NFCI: '金融状况指数',
};

// ── init ───────────────────────────────────────────────────────────────────
export function initLiquidityView() {
  registerMacroTheme();
  window.addEventListener('theme-changed', onLiqThemeChanged);
  renderShell();
  loadAndRender();
}

function onLiqThemeChanged() {
  Object.entries(charts).forEach(([key, chart]) => {
    const dom = chart.getDom();
    if (!dom || !dom.isConnected) return;
    if (chartObservers[key]) chartObservers[key].disconnect();
    const opts = chart.getOption();
    const next = reThemeECharts(chart, dom, opts);
    charts[key] = next;
    chartObservers[key] = new ResizeObserver(() => next.resize());
    chartObservers[key].observe(dom);
  });
}

function renderShell() {
  const root = document.getElementById('liquidity-view');
  root.innerHTML = '';

  const card = document.createElement('div');
  card.className = 'chart-card liq-root';
  card.innerHTML = `
    <div class="liq-summary" id="liq-summary"><div class="loading">加载中…</div></div>
    <div class="macro-toolbar" id="liq-toolbar"></div>
    <div class="liq-charts" id="liq-charts"></div>
  `;
  root.appendChild(card);

  const toolbar = card.querySelector('#liq-toolbar');
  toolbar.innerHTML = MACRO_DATE_RANGES.map(r =>
    `<button class="macro-range-btn${r.value === dateRange ? ' active' : ''}" data-range="${r.value}">${r.label}</button>`
  ).join('');
  toolbar.querySelectorAll('.macro-range-btn').forEach(btn => {
    btn.addEventListener('click', () => setDateRange(btn.dataset.range));
  });
}

// ── data ───────────────────────────────────────────────────────────────────
async function loadAndRender() {
  const summaryEl = document.getElementById('liq-summary');
  const chartsEl = document.getElementById('liq-charts');
  if (summaryEl) summaryEl.innerHTML = '<div class="loading">加载中…</div>';
  if (chartsEl) chartsEl.innerHTML = '';

  try {
    const res = await fetch(`/api/liquidity/overview_${dateRange}.json`);
    if (!res.ok) throw new Error(`API ${res.status}`);
    overviewData = await res.json();
    renderSummaryCards();
    renderCharts();
  } catch (e) {
    if (summaryEl) summaryEl.innerHTML = `<div class="loading">加载失败: ${e.message}</div>`;
  }
}

// ── formatters ─────────────────────────────────────────────────────────────
function fmtVal(v) {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(1) + '万亿';
  if (a >= 1e8) return (v / 1e8).toFixed(0) + '亿';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万';
  return v.toFixed(2);
}

function fmtPct(p) {
  if (p == null) return '<span style="color:#999">—</span>';
  const cls = p > 0 ? 'up' : p < 0 ? 'down' : '';
  return `<span class="${cls}">${p > 0 ? '+' : ''}${(p * 100).toFixed(1)}%</span>`;
}

// ── summary cards ──────────────────────────────────────────────────────────
function renderSummaryCards() {
  const el = document.getElementById('liq-summary');
  if (!el || !overviewData) return;
  el.innerHTML = '';
  highlightedCard = null;

  const cards = ['WALCL', 'RRPONTSYD', 'WRESBAL', 'WTREGEN'];
  cards.forEach(key => {
    const s = overviewData.summary[key];
    if (!s) return;
    const d = document.createElement('div');
    d.className = 'liq-card';
    d.dataset.metric = key;
    d.innerHTML = `
      <div class="liq-card-label" style="color:${COLORS[key]}">${s.label || key}</div>
      <div class="liq-card-value">${fmtVal(s.latest_value)}</div>
      <div class="liq-card-changes">
        <div>1M ${fmtPct(s.change_1m)}</div>
        <div>1Y ${fmtPct(s.change_1y)}</div>
      </div>
    `;
    d.addEventListener('click', () => toggleHighlight(key));
    el.appendChild(d);
  });

  const nl = overviewData.summary.NET_LIQUIDITY;
  if (nl) {
    const d = document.createElement('div');
    d.className = 'liq-card liq-card-wide';
    d.dataset.metric = 'NET_LIQUIDITY';
    d.innerHTML = `
      <div class="liq-card-label" style="color:${COLORS.NET_LIQUIDITY}">${nl.label || '净流动性'}</div>
      <div class="liq-card-value">${fmtVal(nl.latest_value)}</div>
      <div class="liq-card-changes">
        <div>1M ${fmtPct(nl.change_1m)}</div>
        <div>1Y ${fmtPct(nl.change_1y)}</div>
      </div>
    `;
    d.addEventListener('click', () => toggleHighlight('NET_LIQUIDITY'));
    el.appendChild(d);
  }
}

// ── charts ─────────────────────────────────────────────────────────────────
function destroyCharts() {
  Object.entries(chartObservers).forEach(([k, obs]) => { try { obs.disconnect(); } catch {} });
  Object.values(charts).forEach(c => { try { c.dispose(); } catch {} });
  charts = {};
  chartObservers = {};
}

function toSeries(data) {
  if (!data) return [];
  return data.filter(d => d.value != null).map(d => [d.date, d.value]);
}

function observe(key, chart, el) {
  const observer = new ResizeObserver(() => chart.resize());
  observer.observe(el);
  chartObservers[key] = observer;
}

function renderCharts() {
  destroyCharts();
  const container = document.getElementById('liq-charts');
  if (!container || !overviewData) return;
  container.innerHTML = '';

  renderStackedArea(container);
  renderSeesaw(container);
  renderNFCI(container);
}

function makeChartWrap(parent, title, id, height) {
  const wrap = document.createElement('div');
  wrap.className = 'liq-chart-section';
  wrap.innerHTML = `<div class="liq-chart-title">${title}</div><div id="${id}" style="height:${height}px;width:100%"></div>`;
  parent.appendChild(wrap);
  return wrap.querySelector(`#${id}`);
}

function renderStackedArea(parent) {
  const sd = overviewData.stacked;
  const nlRaw = overviewData.series.NET_LIQUIDITY;
  if (!sd) return;

  const el = makeChartWrap(parent, '📊 美联储资产负债表（堆叠面积图）', 'liq-stacked-chart', 350);
  const chart = echarts.init(el, 'macro', { renderer: 'canvas' });
  charts.stacked = chart;
  observe('stacked', chart, el);

  const stackKeys = ['WRESBAL', 'WTREGEN', 'RRPONTSYD'];
  const series = stackKeys.map(key => {
    const data = toSeries(sd[key]);
    return {
      name: LABELS[key], type: 'line', data,
      lineStyle: { color: COLORS[key], width: 1 },
      itemStyle: { color: COLORS[key] },
      areaStyle: { color: COLORS[key] + '44' },
      stack: 'total',
      showSymbol: false,
      emphasis: { focus: 'series' },
    };
  });

  const nlData = toSeries(nlRaw);
  if (nlData.length) {
    series.push({
      name: LABELS.NET_LIQUIDITY, type: 'line', data: nlData,
      lineStyle: { color: COLORS.NET_LIQUIDITY, width: 2 },
      itemStyle: { color: COLORS.NET_LIQUIDITY },
      showSymbol: false,
      emphasis: { focus: 'series' },
    });
  }

  chart.setOption({
    legend: { top: 4, right: 8 },
    grid: { left: '3%', right: '4%', bottom: 40, top: 40, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    yAxis: { type: 'value', scale: true, splitNumber: 4 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series,
  });
}

function renderSeesaw(parent) {
  const rrpData = toSeries(overviewData.series.RRPONTSYD);
  const resData = toSeries(overviewData.series.WRESBAL);
  if (!rrpData.length && !resData.length) return;

  const el = makeChartWrap(parent, '📊 跷跷板图: RRP vs 准备金余额', 'liq-seesaw-chart', 280);
  const chart = echarts.init(el, 'macro', { renderer: 'canvas' });
  charts.seesaw = chart;
  observe('seesaw', chart, el);

  const series = [];
  if (rrpData.length) {
    series.push({
      name: LABELS.RRPONTSYD, type: 'line', data: rrpData,
      lineStyle: { color: COLORS.RRPONTSYD, width: 2 },
      itemStyle: { color: COLORS.RRPONTSYD },
      showSymbol: false, yAxisIndex: 0,
      emphasis: { focus: 'series' },
    });
  }
  if (resData.length) {
    series.push({
      name: LABELS.WRESBAL, type: 'line', data: resData,
      lineStyle: { color: COLORS.WRESBAL, width: 2 },
      itemStyle: { color: COLORS.WRESBAL },
      showSymbol: false, yAxisIndex: 1,
      emphasis: { focus: 'series' },
    });
  }

  chart.setOption({
    legend: { top: 4, right: 8 },
    grid: { left: '3%', right: '8%', bottom: 40, top: 40, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    yAxis: [
      { type: 'value', scale: true, splitNumber: 4, name: 'RRP' },
      { type: 'value', scale: true, splitNumber: 4, name: '准备金' },
    ],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series,
  });
}

function renderNFCI(parent) {
  const nfciData = toSeries(overviewData.series.NFCI);
  if (!nfciData.length) return;

  const el = makeChartWrap(parent, '📊 NFCI 金融状况指数', 'liq-nfci-chart', 200);
  const chart = echarts.init(el, 'macro', { renderer: 'canvas' });
  charts.nfci = chart;
  observe('nfci', chart, el);

  chart.setOption({
    grid: { left: '3%', right: '4%', bottom: 40, top: 16, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    yAxis: { type: 'value', scale: true, splitNumber: 4 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series: [{
      name: LABELS.NFCI, type: 'line', data: nfciData,
      lineStyle: { color: COLORS.NFCI, width: 2 },
      itemStyle: { color: COLORS.NFCI },
      showSymbol: false,
      markLine: { data: [{ yAxis: 0 }], lineStyle: { color: '#999', type: 'dashed', width: 1 }, silent: true, symbol: 'none', label: { show: false } },
    }],
  });
}

// ── card highlight ─────────────────────────────────────────────────────────
function toggleHighlight(metric) {
  highlightedCard = highlightedCard === metric ? null : metric;
  document.querySelectorAll('.liq-card').forEach(c =>
    c.classList.toggle('highlighted', c.dataset.metric === highlightedCard));
  applyDim(highlightedCard);
}

function applyDim(highlightMetric) {
  Object.values(charts).forEach(chart => {
    const opt = chart.getOption();
    if (!opt.series) return;
    const newSeries = opt.series.map(s => {
      const matched = Object.entries(LABELS).find(([, v]) => v === s.name);
      const key = matched ? matched[0] : null;
      const dimmed = highlightMetric && key !== highlightMetric;
      const baseColor = COLORS[key];
      if (!baseColor) return {};
      return {
        lineStyle: { color: dimmed ? baseColor + '33' : baseColor },
        itemStyle: { color: dimmed ? baseColor + '33' : baseColor },
      };
    });
    chart.setOption({ series: newSeries }, false);
  });
}

// ── date range ─────────────────────────────────────────────────────────────
function setDateRange(range) {
  if (dateRange === range) return;
  dateRange = range;
  document.querySelectorAll('#liq-toolbar .macro-range-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.range === range));
  loadAndRender();
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '流动性';
  document.getElementById('status-count').textContent = overviewData ? '5 指标' : '';
  document.getElementById('status-range').textContent = '';
}
