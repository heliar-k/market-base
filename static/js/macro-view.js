// macro-view.js — macro panel with ECharts

import { registerMacroTheme } from './echarts-theme.js';
import { MACRO_COLORS, MACRO_LABELS, MACRO_DATE_RANGES, applyDateFilter } from './macro-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let macroCategories = null;
let macroCache = {};
let macroChartInstances = {}; // name → { chart, observer }
let macroDateRange = '10y';

const TERM_SERIES_LABELS = {
  'rates': { DGS1MO: '1M', DGS3MO: '3M', DGS6MO: '6M', DGS1: '1Y', DGS2: '2Y',
             DGS3: '3Y', DGS5: '5Y', DGS7: '7Y', DGS10: '10Y', DGS20: '20Y', DGS30: '30Y' },
  'tips': { DFII5: '5Y', DFII7: '7Y', DFII10: '10Y', DFII20: '20Y', DFII30: '30Y' },
};

// ── helpers ────────────────────────────────────────────────────────────────
function disposeChart(name) {
  const entry = macroChartInstances[name];
  if (!entry) return;
  if (entry.observer) entry.observer.disconnect();
  entry.chart.dispose();
  delete macroChartInstances[name];
}

function disposeAllCharts() {
  Object.keys(macroChartInstances).forEach(disposeChart);
}

function observe(chart, el) {
  const observer = new ResizeObserver(() => chart.resize());
  observer.observe(el);
  return observer;
}

// ── init ───────────────────────────────────────────────────────────────────
export function initMacroView() {
  registerMacroTheme();
  loadMacroCategories();
}

async function loadMacroCategories() {
  const res = await fetch('/api/macro/categories');
  macroCategories = await res.json();
  renderMacroSections();
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
    section.innerHTML = `<div class="macro-section-header" data-cat="${cat.name}"><span class="macro-expand">▶</span><span class="macro-cat-name">${catNameLabel(cat.name)}</span><span class="macro-cat-count">${cat.series.length} 项</span></div><div class="macro-section-body"><div class="macro-series-charts" id="macro-charts-${cat.name}"></div></div>`;
    section.querySelector('.macro-section-header').addEventListener('click', () => toggleMacroSection(cat.name, cat.series, section));
    grid.appendChild(section);
  });
  container.appendChild(grid);
}

function catNameLabel(name) {
  if (name === 'fx') return 'FX';
  return name.charAt(0).toUpperCase() + name.slice(1);
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

  if (name === 'rates' || name === 'tips') {
    renderTermCategory(name, seriesKeys, chartsContainer);
  } else {
    renderStandardCategory(name, seriesKeys, chartsContainer);
  }
}

// ── standard category: multi-series line chart in one ECharts instance ─────
function renderStandardCategory(name, seriesKeys, container) {
  const rawData = macroCache[name];
  if (!rawData || rawData.length === 0) return;

  const data = applyDateFilter(rawData, macroDateRange);
  const activeKeys = seriesKeys.filter(k => data.some(d => d[k] != null));
  if (activeKeys.length === 0) return;

  const chartWrap = document.createElement('div');
  chartWrap.className = 'macro-series-chart';
  chartWrap.id = `macro-ec-${name}`;
  chartWrap.style.cssText = 'height:360px;width:100%';
  container.appendChild(chartWrap);

  const series = activeKeys.map((key, idx) => ({
    name: MACRO_LABELS[key] || key,
    type: 'line',
    data: data.map(d => [d.date, d[key]]),
    lineStyle: { width: 1.5 },
    itemStyle: { color: MACRO_COLORS[idx % MACRO_COLORS.length] },
    showSymbol: false,
    emphasis: { focus: 'series' },
  }));

  const chart = echarts.init(chartWrap, 'macro', { renderer: 'canvas' });
  chart.setOption({
    legend: { show: activeKeys.length > 1, top: 4, right: 8 },
    grid: { left: '3%', right: '4%', bottom: 56, top: activeKeys.length > 1 ? 40 : 16, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    yAxis: { type: 'value', scale: true, splitNumber: 4 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series,
  });

  macroChartInstances[name] = { chart, observer: observe(chart, chartWrap) };
}

// ── term structure category (rates/tips): line chart ───────────────────────
function renderTermCategory(name, seriesKeys, container) {
  const rawData = macroCache[name];
  if (!rawData || rawData.length === 0) return;

  const termLabels = TERM_SERIES_LABELS[name] || {};
  const data = applyDateFilter(rawData, macroDateRange);

  const latest = data[data.length - 1];
  const activeKeys = seriesKeys.filter(k => latest && latest[k] != null);
  if (activeKeys.length === 0) return;

  const termData = activeKeys.map(k => {
    const label = termLabels[k] || k;
    return [label, latest[k]];
  });

  const chartWrap = document.createElement('div');
  chartWrap.className = 'macro-series-chart';
  chartWrap.id = `macro-ec-${name}`;
  chartWrap.style.cssText = 'height:360px;width:100%';
  container.appendChild(chartWrap);

  const chart = echarts.init(chartWrap, 'macro', { renderer: 'canvas' });
  chart.setOption({
    title: { text: '期限结构快照 (' + latest.date + ')', left: 8, top: 4 },
    grid: { left: '3%', right: '4%', bottom: 24, top: 52, containLabel: true },
    xAxis: { type: 'category', data: termData.map(d => d[0]), boundaryGap: false },
    yAxis: { type: 'value', scale: true, splitNumber: 5, axisLabel: { formatter: '{value}%' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, valueFormatter: v => v != null ? v.toFixed(2) + '%' : '-' },
    series: [{
      name: name === 'rates' ? '名义收益率' : 'TIPS 实际利率',
      type: 'line',
      data: termData.map(d => d[1]),
      lineStyle: { color: '#1a73e8', width: 2 },
      itemStyle: { color: '#1a73e8' },
      symbol: 'circle', symbolSize: 6,
      label: { show: true, position: 'top', fontSize: 10, formatter: p => p.value != null ? p.value.toFixed(2) + '%' : '' },
    }],
  });

  macroChartInstances[name] = { chart, observer: observe(chart, chartWrap) };
}

// ── date range toggle ──────────────────────────────────────────────────────
function setMacroDateRange(range) {
  if (macroDateRange === range) return;
  macroDateRange = range;
  document.querySelectorAll('.macro-range-btn').forEach(b => b.classList.toggle('active', b.dataset.range === range));

  disposeAllCharts();

  macroCategories.forEach(cat => {
    const sections = document.querySelectorAll(`.macro-section-header[data-cat="${cat.name}"]`);
    sections.forEach(header => {
      const section = header.closest('.macro-section');
      if (section && section.classList.contains('open')) {
        const chartsContainer = document.getElementById('macro-charts-' + cat.name);
        if (chartsContainer) {
          chartsContainer.innerHTML = '';
          if (cat.name === 'rates' || cat.name === 'tips') {
            renderTermCategory(cat.name, cat.series, chartsContainer);
          } else {
            renderStandardCategory(cat.name, cat.series, chartsContainer);
          }
        }
      }
    });
  });
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = macroCategories ? `${macroCategories.length} 分类` : '';
  document.getElementById('status-range').textContent = '';
}
