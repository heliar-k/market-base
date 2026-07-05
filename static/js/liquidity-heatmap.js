// liquidity-heatmap.js — liquidity overview: summary cards + stacked/seesaw/NFCI charts

import { CHART_OPTS, addLine, initTooltip } from './charts-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let overviewData = null;
let dateRange = 'all';
let charts = {};
let highlightedCard = null;
const seriesRefs = {}; // metric → series instance (for highlight dimming)

const COLORS = {
  WALCL: '#1a73e8', RRPONTSYD: '#ff9800', WRESBAL: '#26a69a',
  WTREGEN: '#e91e63', NET_LIQUIDITY: '#9c27b0', NFCI: '#607d8b',
};

const LABELS = {
  WALCL: '美联储总资产', RRPONTSYD: '隔夜逆回购', WRESBAL: '准备金余额',
  WTREGEN: '财政部一般账户余额', NET_LIQUIDITY: '净流动性', NFCI: '金融状况指数',
};

const DATE_RANGES = [
  { label: '1M', value: '1m' }, { label: '3M', value: '3m' },
  { label: '6M', value: '6m' }, { label: '1Y', value: '1y' },
  { label: '2Y', value: '2y' }, { label: '3Y', value: '3y' },
  { label: '5Y', value: '5y' }, { label: '10Y', value: '10y' },
  { label: 'All', value: 'all' },
];

// ── init ───────────────────────────────────────────────────────────────────
export function initLiquidityView() {
  renderShell();
  loadAndRender();
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

  // Date range buttons
  const toolbar = card.querySelector('#liq-toolbar');
  toolbar.innerHTML = DATE_RANGES.map(r =>
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
    const res = await fetch(`/api/liquidity/overview?range=${encodeURIComponent(dateRange)}`);
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

  // NET_LIQUIDITY wide card
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
  Object.values(charts).forEach(c => { try { c.remove(); } catch {} });
  charts = {};
  for (const k of Object.keys(seriesRefs)) delete seriesRefs[k];
}

function toSeries(data) {
  return (data || []).filter(d => d.value != null).map(d => ({ time: d.date, value: d.value }));
}

function renderCharts() {
  destroyCharts();
  const container = document.getElementById('liq-charts');
  if (!container || !overviewData) return;
  container.innerHTML = '';

  renderStackedArea(container);
  renderSeesaw(container);
  renderNFCI(container);
  applyHighlight();
}

function makeChartDiv(parent, id, title, height) {
  const wrap = document.createElement('div');
  wrap.className = 'liq-chart-section';
  wrap.innerHTML = `<div class="liq-chart-title">${title}</div><div id="${id}" style="height:${height}px;position:relative"></div>`;
  parent.appendChild(wrap);
  return wrap.querySelector(`#${id}`);
}

function renderStackedArea(parent) {
  const sd = overviewData.stacked;
  const nlRaw = overviewData.series.NET_LIQUIDITY;
  if (!sd) return;

  const el = makeChartDiv(parent, 'liq-stacked-chart', '📊 美联储资产负债表（堆叠面积图）', 350);

  const chart = LightweightCharts.createChart(el, {
    ...CHART_OPTS,
    leftPriceScale: { visible: false },
    rightPriceScale: { visible: true, borderColor: '#e1e4e8', autoScale: true, scaleMargins: { top: 0.05, bottom: 0.05 } },
  });
  charts.stacked = chart;

  const addArea = (stackedKey, color, label) => {
    const data = toSeries(sd[stackedKey]);
    if (!data.length) return null;
    const s = chart.addAreaSeries({
      topColor: color + '66', bottomColor: color + '11', lineColor: color,
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s._seriesName = stackedKey;
    s._label = label;
    s._baseColor = color;
    s._isArea = true;
    s.setData(data);
    (seriesRefs[stackedKey] = seriesRefs[stackedKey] || []).push(s);
    return s;
  };

  // Bottom-to-top: reserves → TGA → RRP
  addArea('WRESBAL', COLORS.WRESBAL, LABELS.WRESBAL);
  addArea('WTREGEN', COLORS.WTREGEN, LABELS.WTREGEN);
  addArea('RRPONTSYD', COLORS.RRPONTSYD, LABELS.RRPONTSYD);

  // NET_LIQUIDITY line overlay
  const nlData = toSeries(nlRaw);
  if (nlData.length) {
    const s = chart.addLineSeries({
      color: COLORS.NET_LIQUIDITY, lineWidth: 2,
      priceLineVisible: true, lastValueVisible: true,
    });
    s._seriesName = 'NET_LIQUIDITY';
    s._label = LABELS.NET_LIQUIDITY;
    s._baseColor = COLORS.NET_LIQUIDITY;
    s.setData(nlData);
    (seriesRefs.NET_LIQUIDITY = seriesRefs.NET_LIQUIDITY || []).push(s);
  }

  initTooltip(chart, 'liq-stacked-chart', LABELS);
}

function renderSeesaw(parent) {
  const rrpData = toSeries(overviewData.series.RRPONTSYD);
  const resData = toSeries(overviewData.series.WRESBAL);
  if (!rrpData.length && !resData.length) return;

  const el = makeChartDiv(parent, 'liq-seesaw-chart', '📊 跷跷板图: RRP vs 准备金余额', 280);

  const chart = LightweightCharts.createChart(el, {
    ...CHART_OPTS,
    leftPriceScale: { visible: true, borderColor: '#e1e4e8', autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
    rightPriceScale: { visible: true, borderColor: '#e1e4e8', autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
  });
  charts.seesaw = chart;

  if (rrpData.length) {
    const s = chart.addLineSeries({
      color: COLORS.RRPONTSYD, lineWidth: 2, priceScaleId: 'left',
      priceLineVisible: true, lastValueVisible: true,
    });
    s._seriesName = 'RRPONTSYD';
    s._baseColor = COLORS.RRPONTSYD;
    s.setData(rrpData);
    (seriesRefs.RRPONTSYD = seriesRefs.RRPONTSYD || []).push(s);
  }

  if (resData.length) {
    const s = chart.addLineSeries({
      color: COLORS.WRESBAL, lineWidth: 2, priceScaleId: 'right',
      priceLineVisible: true, lastValueVisible: true,
    });
    s._seriesName = 'WRESBAL';
    s._baseColor = COLORS.WRESBAL;
    s.setData(resData);
    (seriesRefs.WRESBAL = seriesRefs.WRESBAL || []).push(s);
  }

  initTooltip(chart, 'liq-seesaw-chart', LABELS);
}

function renderNFCI(parent) {
  const nfciData = toSeries(overviewData.series.NFCI);
  if (!nfciData.length) return;

  const el = makeChartDiv(parent, 'liq-nfci-chart', '📊 NFCI 金融状况指数', 200);

  const chart = LightweightCharts.createChart(el, { ...CHART_OPTS, height: 200 });
  charts.nfci = chart;

  const s = addLine(chart, nfciData, COLORS.NFCI, 2, 0, true);
  s._seriesName = 'NFCI';
  s._baseColor = COLORS.NFCI;
  (seriesRefs.NFCI = seriesRefs.NFCI || []).push(s);
  initTooltip(chart, 'liq-nfci-chart', LABELS);
}

// ── card highlight ─────────────────────────────────────────────────────────
function toggleHighlight(metric) {
  highlightedCard = highlightedCard === metric ? null : metric;
  document.querySelectorAll('.liq-card').forEach(c =>
    c.classList.toggle('highlighted', c.dataset.metric === highlightedCard));
  applyHighlight();
}

function applyHighlight() {
  const ALPHA_DIM = '22';
  Object.entries(seriesRefs).forEach(([metric, refs]) => {
    const dimmed = highlightedCard && metric !== highlightedCard;
    refs.forEach(s => {
      try {
        const c = s._baseColor;
        if (s._isArea) {
          s.applyOptions({
            topColor: dimmed ? c + ALPHA_DIM : c + '66',
            bottomColor: dimmed ? c + ALPHA_DIM : c + '11',
            lineColor: dimmed ? c + ALPHA_DIM : c,
          });
        } else {
          s.applyOptions({ color: dimmed ? c + ALPHA_DIM : c });
        }
      } catch {}
    });
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
