// cross-correlation.js — cross-period correlation overlay chart

import { CHART_OPTS, initTooltip } from './charts-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let presets = null;
let activePreset = null;
let corrChart = null;
let dateRange = 'all';

const COLORS = ['#2196f3','#ff9800','#4caf50','#e91e63','#9c27b0','#00bcd4','#ff5722','#607d8b','#795548','#3f51b5'];

const LABELS = {
  VIX: '波动率指数（恐慌指数）', HY_OAS: '高收益债信用利差', IG_OAS: '投资级债信用利差',
  CPI: '消费者物价指数', PCE: '个人消费支出价格指数', CORE_CPI: '核心消费者物价指数',
  T5YIE: '5年期通胀预期', T10YIE: '10年期通胀预期', T5YIFR: '5年期远期通胀率',
  MICH: '密歇根通胀预期', EXPINF_1Y: '1年期通胀预期', EXPINF_2Y: '2年期通胀预期',
  EXPINF_5Y: '5年期通胀预期', EXPINF_10Y: '10年期通胀预期',
  UNRATE: '失业率', PAYEMS: '非农就业人数', ICSA: '初请失业金人数',
  GDP: '国内生产总值(GDP)', INDPRO: '工业生产指数',
  FEDFUNDS: '联邦基金利率', SOFR: '担保隔夜融资利率', IORB: '准备金余额利率',
  DGS1MO: '1月期国债收益率', DGS3MO: '3月期国债收益率', DGS6MO: '6月期国债收益率',
  DGS1: '1年期国债收益率', DGS2: '2年期国债收益率', DGS3: '3年期国债收益率',
  DGS5: '5年期国债收益率', DGS7: '7年期国债收益率', DGS10: '10年期国债收益率',
  DGS20: '20年期国债收益率', DGS30: '30年期国债收益率',
  SPREAD_2S10S: '2s10s利差', SPREAD_3M10S: '3m10s利差', SPREAD_5S30S: '5s30s利差',
  SOFR_IORB_SPREAD_BP: 'SOFR-IORB利差(bp)',
  DFII5: '5年期TIPS收益率', DFII7: '7年期TIPS收益率', DFII10: '10年期TIPS收益率',
  DFII20: '20年期TIPS收益率', DFII30: '30年期TIPS收益率',
  BEI_5Y: '5年期盈亏平衡通胀率', BEI_7Y: '7年期盈亏平衡通胀率',
  BEI_10Y: '10年期盈亏平衡通胀率', BEI_20Y: '20年期盈亏平衡通胀率',
  BEI_30Y: '30年期盈亏平衡通胀率',
  NFCI: '金融状况指数', RRPONTSYD: '隔夜逆回购规模', WTREGEN: '财政部一般账户余额',
  WRESBAL: '准备金余额', WALCL: '美联储总资产', NET_LIQUIDITY: '净流动性',
  UMCSENT: '密歇根消费者信心指数', STLFSI4: '金融压力指数',
  DXY: '美元指数',
};

const DATE_RANGES = [
  { label: '1M', value: '1m', months: 1 },
  { label: '3M', value: '3m', months: 3 },
  { label: '6M', value: '6m', months: 6 },
  { label: '1Y', value: '1y', months: 12 },
  { label: '2Y', value: '2y', months: 24 },
  { label: '3Y', value: '3y', months: 36 },
  { label: '5Y', value: '5y', months: 60 },
  { label: '10Y', value: '10y', months: 120 },
  { label: '30Y', value: '30y', months: 360 },
  { label: 'All', value: 'all', months: 0 },
];

const ALL_INDICATORS = {
  volatility: ['VIX', 'HY_OAS', 'IG_OAS'],
  inflation: ['CPI', 'PCE', 'CORE_CPI', 'T5YIE', 'T10YIE', 'T5YIFR', 'MICH', 'EXPINF_1Y', 'EXPINF_2Y', 'EXPINF_5Y', 'EXPINF_10Y'],
  labor: ['UNRATE', 'PAYEMS', 'ICSA'],
  growth: ['GDP', 'INDPRO'],
  rates: ['FEDFUNDS', 'SOFR', 'IORB', 'DGS1MO', 'DGS3MO', 'DGS6MO', 'DGS1', 'DGS2', 'DGS3', 'DGS5', 'DGS7', 'DGS10', 'DGS20', 'DGS30'],
  tips: ['DFII5', 'DFII7', 'DFII10', 'DFII20', 'DFII30'],
  liquidity: ['NFCI', 'RRPONTSYD', 'WTREGEN', 'WRESBAL', 'WALCL'],
  sentiment: ['UMCSENT', 'STLFSI4'],
  fx: ['DXY'],
  derived: ['SPREAD_2S10S', 'SPREAD_3M10S', 'SPREAD_5S30S', 'NET_LIQUIDITY', 'BEI_5Y', 'BEI_7Y', 'BEI_10Y', 'BEI_20Y', 'BEI_30Y', 'SOFR_IORB_SPREAD_BP'],
};

// ── init ───────────────────────────────────────────────────────────────────
export async function initCorrelationView() {
  try {
    const res = await fetch('/api/macro/presets');
    const json = await res.json();
    presets = json.presets;
  } catch {
    presets = [];
  }
  renderUI();
  if (presets.length) loadPreset(presets[0]);
}

// ── UI ─────────────────────────────────────────────────────────────────────
function renderUI() {
  const card = document.getElementById('correlation-card');
  card.innerHTML = '';

  // Preset buttons
  const presetsBar = document.createElement('div');
  presetsBar.className = 'correlation-presets';
  presetsBar.innerHTML = presets.map(p =>
    `<button class="correlation-preset-btn" data-id="${p.id}">${p.name}</button>`
  ).join('');
  presetsBar.querySelectorAll('.correlation-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const preset = presets.find(p => p.id === btn.dataset.id);
      if (preset) loadPreset(preset);
    });
  });
  card.appendChild(presetsBar);

  // Controls row: date toolbar + custom indicator selectors
  const controls = document.createElement('div');
  controls.className = 'correlation-controls';

  const dateToolbar = document.createElement('div');
  dateToolbar.className = 'correlation-date-toolbar';
  dateToolbar.innerHTML = DATE_RANGES.map(r =>
    `<button class="macro-range-btn${r.value === dateRange ? ' active' : ''}" data-range="${r.value}">${r.label}</button>`
  ).join('');
  dateToolbar.querySelectorAll('.macro-range-btn').forEach(btn => {
    btn.addEventListener('click', () => setDateRange(btn.dataset.range));
  });
  controls.appendChild(dateToolbar);

  const custom = document.createElement('div');
  custom.className = 'correlation-custom';
  custom.id = 'correlation-custom-controls';
  controls.appendChild(custom);

  card.appendChild(controls);

  // Chart container
  const chartDiv = document.createElement('div');
  chartDiv.id = 'correlation-chart';
  card.appendChild(chartDiv);

  renderCustomControls();
}

function renderCustomControls() {
  const container = document.getElementById('correlation-custom-controls');
  if (!container) return;
  container.innerHTML = '';

  if (!activePreset) return;

  const indicators = activePreset.indicators;

  indicators.forEach((ind, idx) => {
    const chip = document.createElement('div');
    chip.className = 'correlation-indicator-chip';

    const dot = document.createElement('span');
    dot.style.cssText = `width:8px;height:8px;border-radius:50%;background:${COLORS[idx % COLORS.length]}`;
    chip.appendChild(dot);

    const axis = activePreset.left_axis.includes(ind) ? 'L' : 'R';
    const label = document.createElement('span');
    label.textContent = `${ind} (${axis})`;
    chip.appendChild(label);

    if (indicators.length > 2) {
      const remove = document.createElement('span');
      remove.className = 'correlation-chip-remove';
      remove.textContent = '×';
      remove.addEventListener('click', () => removeIndicator(ind));
      chip.appendChild(remove);
    }

    container.appendChild(chip);
  });

  if (indicators.length < 5) {
    const addBtn = document.createElement('button');
    addBtn.className = 'correlation-add-btn';
    addBtn.textContent = '+ 添加指标';
    addBtn.addEventListener('click', () => showAddIndicator(addBtn));
    container.appendChild(addBtn);
  }
}

function showAddIndicator(btn) {
  const existing = new Set(activePreset.indicators);
  const select = document.createElement('select');
  select.className = 'correlation-select';

  // Group indicators by category
  for (const [cat, metrics] of Object.entries(ALL_INDICATORS)) {
    const optgroup = document.createElement('optgroup');
    optgroup.label = cat === 'fx' ? 'FX' : cat.charAt(0).toUpperCase() + cat.slice(1);
    metrics.forEach(m => {
      if (!existing.has(m)) {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = `${m} — ${LABELS[m] || m}`;
        optgroup.appendChild(opt);
      }
    });
    if (optgroup.children.length) select.appendChild(optgroup);
  }

  btn.replaceWith(select);
  select.addEventListener('change', () => {
    if (select.value) {
      addIndicator(select.value);
    }
  });
}

// ── data loading ───────────────────────────────────────────────────────────
async function loadPreset(preset) {
  activePreset = { ...preset };

  document.querySelectorAll('.correlation-preset-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.id === preset.id));

  renderCustomControls();
  await loadAndRender();
}

async function loadAndRender() {
  if (!activePreset) return;

  const indicators = activePreset.indicators.join(',');
  try {
    const res = await fetch(`/api/macro/correlate?indicators=${encodeURIComponent(indicators)}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const json = await res.json();

    const seriesMap = {};
    for (const [name, info] of Object.entries(json.indicators)) {
      const filtered = filterByDateRange(info.data);
      seriesMap[name] = {
        data: filtered.filter(d => d.value != null).map(d => ({ time: d.date, value: d.value })),
        label: info.label,
        category: info.category,
      };
    }

    renderChart(seriesMap);
  } catch (e) {
    const chartEl = document.getElementById('correlation-chart');
    if (chartEl) chartEl.innerHTML = `<div class="loading">加载失败: ${e.message}</div>`;
  }
}

function filterByDateRange(data) {
  if (dateRange === 'all' || !data || !data.length) return data;
  const rangeInfo = DATE_RANGES.find(r => r.value === dateRange);
  if (!rangeInfo || !rangeInfo.months) return data;
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - rangeInfo.months);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return data.filter(d => d.date >= cutoffStr);
}

// ── chart rendering ────────────────────────────────────────────────────────
function renderChart(seriesMap) {
  // Destroy previous chart
  if (corrChart) {
    corrChart.remove();
    corrChart = null;
  }

  const chartEl = document.getElementById('correlation-chart');
  chartEl.innerHTML = '';

  if (!activePreset) return;

  const chart = LightweightCharts.createChart(chartEl, {
    ...CHART_OPTS,
    leftPriceScale: { visible: true, borderColor: '#e1e4e8', autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
    rightPriceScale: { visible: true, borderColor: '#e1e4e8', autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
  });
  corrChart = chart;

  const labels = {};

  activePreset.indicators.forEach((name, idx) => {
    const info = seriesMap[name];
    if (!info || !info.data.length) return;

    const color = COLORS[idx % COLORS.length];
    const isLeft = activePreset.left_axis.includes(name);

    const series = chart.addLineSeries({
      color,
      lineWidth: 2,
      priceScaleId: isLeft ? 'left' : 'right',
      lastValueVisible: true,
      priceLineVisible: false,
    });
    series._seriesName = name;
    series.setData(info.data);

    labels[name] = info.label;
  });

  initTooltip(chart, 'correlation-chart', labels);
}

// ── indicator management ───────────────────────────────────────────────────
function addIndicator(name) {
  if (!activePreset || activePreset.indicators.includes(name)) return;
  activePreset.indicators = [...activePreset.indicators, name];
  // New indicator goes to right axis
  activePreset.right_axis = [...activePreset.right_axis, name];
  // Clear preset highlight since user modified it
  activePreset.id = 'custom';
  document.querySelectorAll('.correlation-preset-btn').forEach(btn => btn.classList.remove('active'));
  renderCustomControls();
  loadAndRender();
}

function removeIndicator(name) {
  if (!activePreset) return;
  activePreset.indicators = activePreset.indicators.filter(i => i !== name);
  activePreset.left_axis = activePreset.left_axis.filter(i => i !== name);
  activePreset.right_axis = activePreset.right_axis.filter(i => i !== name);
  activePreset.id = 'custom';
  document.querySelectorAll('.correlation-preset-btn').forEach(btn => btn.classList.remove('active'));
  renderCustomControls();
  loadAndRender();
}

// ── date range ─────────────────────────────────────────────────────────────
function setDateRange(range) {
  if (dateRange === range) return;
  dateRange = range;
  document.querySelectorAll('.correlation-date-toolbar .macro-range-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range));
  loadAndRender();
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '关联';
  document.getElementById('status-count').textContent = activePreset
    ? `${activePreset.indicators.length} 指标` : '';
  document.getElementById('status-range').textContent = activePreset
    ? activePreset.name || '' : '';
}
