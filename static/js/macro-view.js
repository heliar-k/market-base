// macro-view.js — macro panel: categories, charts, date filtering

import { CHART_OPTS, addLine, initTooltip } from './charts-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let macroCategories = null;
let macroCache = {};
let macroCharts = {};
let macroChartSeries = {};
let macroDateRange = '10y';

const MACRO_COLORS = ['#2196f3','#ff9800','#4caf50','#e91e63','#9c27b0','#00bcd4','#ff5722','#607d8b','#795548','#3f51b5'];

const MACRO_LABELS = {
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
  SPREAD_2S10S: '2s10s利差', SOFR_IORB_SPREAD_BP: 'SOFR-IORB利差(bp)',
  DFII5: '5年期TIPS收益率', DFII7: '7年期TIPS收益率', DFII10: '10年期TIPS收益率',
  DFII20: '20年期TIPS收益率', DFII30: '30年期TIPS收益率',
  BEI_5Y: '5年期盈亏平衡通胀率', BEI_10Y: '10年期盈亏平衡通胀率',
  NFCI: '金融状况指数', RRPONTSYD: '隔夜逆回购规模', WTREGEN: '财政部一般账户余额',
  WRESBAL: '准备金余额', WALCL: '美联储总资产', NET_LIQUIDITY: '净流动性',
  UMCSENT: '密歇根消费者信心指数', STLFSI4: '金融压力指数',
  DXY: '美元指数',
};

const MACRO_DATE_RANGES = [
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

// ── init ───────────────────────────────────────────────────────────────────
export function initMacroView() {
  loadMacroCategories();
}

async function loadMacroCategories() {
  const res = await fetch('/api/macro/categories');
  macroCategories = await res.json();
  renderMacroSections();
}

// ── date filter ────────────────────────────────────────────────────────────
function applyDateFilter(data, range) {
  if (range === 'all' || !data || data.length === 0) return data;
  const rangeInfo = MACRO_DATE_RANGES.find(r => r.value === range);
  if (!rangeInfo || !rangeInfo.months) return data;
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - rangeInfo.months);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return data.filter(d => d.date >= cutoffStr);
}

// ── render ─────────────────────────────────────────────────────────────────
function renderMacroSections() {
  const container = document.getElementById('macro-chart-card');
  container.innerHTML = '';

  const toolbar = document.createElement('div');
  toolbar.className = 'macro-toolbar';
  toolbar.innerHTML = MACRO_DATE_RANGES.map(r =>
    `<button class="macro-range-btn${r.value === macroDateRange ? ' active' : ''}" data-range="${r.value}">${r.label}</button>`
  ).join('');
  toolbar.querySelectorAll('.macro-range-btn').forEach(btn => {
    btn.addEventListener('click', () => setMacroDateRange(btn.dataset.range));
  });
  container.appendChild(toolbar);

  const grid = document.createElement('div');
  grid.className = 'macro-grid';
  macroCategories.forEach(cat => {
    const section = document.createElement('div');
    section.className = 'macro-section collapsed';
    section.innerHTML = `<div class="macro-section-header" data-cat="${cat.name}"><span class="macro-expand">▶</span><span class="macro-cat-name">${cat.name === 'fx' ? 'FX' : cat.name.charAt(0).toUpperCase() + cat.name.slice(1)}</span><span class="macro-cat-count">${cat.series.length} 项</span></div><div class="macro-section-body"><div class="macro-series-charts" id="macro-charts-${cat.name}"></div></div>`;
    section.querySelector('.macro-section-header').addEventListener('click', () => toggleMacroSection(cat.name, cat.series, section));
    grid.appendChild(section);
  });
  container.appendChild(grid);
}

async function toggleMacroSection(name, seriesKeys, section) {
  const isOpen = section.classList.contains('open');
  if (isOpen) {
    section.classList.remove('open');
    section.classList.add('collapsed');
    return;
  }
  section.classList.remove('collapsed');
  section.classList.add('open');

  if (!macroCache[name]) {
    const res = await fetch(`/api/macro/${name}`);
    macroCache[name] = await res.json();
  }

  const chartsContainer = document.getElementById('macro-charts-' + name);
  if (chartsContainer.children.length > 0) return;

  renderMacroCategoryCharts(name, seriesKeys);
}

function renderMacroCategoryCharts(name, seriesKeys) {
  const chartsContainer = document.getElementById('macro-charts-' + name);
  if (!macroCharts[name]) macroCharts[name] = {};
  if (!macroChartSeries[name]) macroChartSeries[name] = {};

  seriesKeys.forEach((key, idx) => {
    const rawData = macroCache[name].filter(d => d[key] != null);
    const data = applyDateFilter(rawData, macroDateRange).map(d => ({ time: d.date, value: d[key] }));
    if (data.length === 0) return;

    const color = MACRO_COLORS[idx % MACRO_COLORS.length];
    const chartWrap = document.createElement('div');
    chartWrap.className = 'macro-series-chart-wrap';
    chartWrap.innerHTML = `<div class="macro-series-label" style="color:${color}">${key}</div><div class="macro-series-chart" id="macro-chart-${name}-${key}" style="height:180px;position:relative"></div>`;
    chartsContainer.appendChild(chartWrap);

    const chartEl = chartWrap.querySelector('.macro-series-chart');
    const chart = LightweightCharts.createChart(chartEl, { ...CHART_OPTS, height: 180 });
    initTooltip(chart, 'macro-chart-' + name + '-' + key, MACRO_LABELS);
    const s = addLine(chart, data, color, 1.5, 0, true);
    s._seriesName = key;
    macroCharts[name][key] = chart;
    macroChartSeries[name][key] = s;
    chart.timeScale().fitContent();
  });

  // sync time scales across all charts in this section
  const charts = Object.values(macroCharts[name]);
  let syncing = false;
  charts.forEach(chart => {
    chart.timeScale().subscribeVisibleTimeRangeChange(() => {
      if (syncing) return;
      syncing = true;
      const range = chart.timeScale().getVisibleRange();
      charts.forEach(other => {
        if (other !== chart) other.timeScale().setVisibleRange(range);
      });
      syncing = false;
    });
  });
}

function setMacroDateRange(range) {
  if (macroDateRange === range) return;
  macroDateRange = range;
  document.querySelectorAll('.macro-range-btn').forEach(b => b.classList.toggle('active', b.dataset.range === range));
  macroCategories.forEach(cat => {
    const section = document.querySelector(`.macro-section-header[data-cat="${cat.name}"]`)?.closest('.macro-section');
    if (section && section.classList.contains('open')) {
      if (!macroCache[cat.name]) return;
      const chartsContainer = document.getElementById('macro-charts-' + cat.name);
      if (!chartsContainer) return;
      delete macroCharts[cat.name];
      delete macroChartSeries[cat.name];
      chartsContainer.innerHTML = '';
      renderMacroCategoryCharts(cat.name, cat.series);
    }
  });
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = macroCategories ? `${macroCategories.length} 分类` : '';
  document.getElementById('status-range').textContent = '';
}
