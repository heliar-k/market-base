// cross-correlation.js — cross-period correlation overlay chart (ECharts)

import { registerMacroTheme, reThemeECharts } from './echarts-theme.js';
import { MACRO_COLORS, MACRO_LABELS, MACRO_DATE_RANGES, applyDateFilter } from './macro-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let presets = null;
let activePreset = null;
let corrChart = null;
let corrObserver = null;
let dateRange = 'all';

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
  registerMacroTheme();
  window.addEventListener('theme-changed', onCorrThemeChanged);
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

function onCorrThemeChanged() {
  if (!corrChart) return;
  const dom = corrChart.getDom();
  if (!dom || !dom.isConnected) return;
  if (corrObserver) corrObserver.disconnect();
  const opts = corrChart.getOption();
  corrChart = reThemeECharts(corrChart, dom, opts);
  corrObserver = new ResizeObserver(() => corrChart.resize());
  corrObserver.observe(dom);
}
  const card = document.getElementById('correlation-card');
  card.innerHTML = '';

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

  const controls = document.createElement('div');
  controls.className = 'correlation-controls';

  const dateToolbar = document.createElement('div');
  dateToolbar.className = 'correlation-date-toolbar';
  dateToolbar.innerHTML = MACRO_DATE_RANGES.map(r =>
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

  activePreset.indicators.forEach((ind, idx) => {
    const chip = document.createElement('div');
    chip.className = 'correlation-indicator-chip';
    const dot = document.createElement('span');
    dot.style.cssText = `width:8px;height:8px;border-radius:50%;background:${MACRO_COLORS[idx % MACRO_COLORS.length]}`;
    chip.appendChild(dot);
    const axis = activePreset.left_axis.includes(ind) ? 'L' : 'R';
    const label = document.createElement('span');
    label.textContent = `${ind} (${axis})`;
    chip.appendChild(label);
    if (activePreset.indicators.length > 2) {
      const remove = document.createElement('span');
      remove.className = 'correlation-chip-remove';
      remove.textContent = '×';
      remove.addEventListener('click', () => removeIndicator(ind));
      chip.appendChild(remove);
    }
    container.appendChild(chip);
  });

  if (activePreset.indicators.length < 5) {
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
  for (const [cat, metrics] of Object.entries(ALL_INDICATORS)) {
    const optgroup = document.createElement('optgroup');
    optgroup.label = cat === 'fx' ? 'FX' : cat.charAt(0).toUpperCase() + cat.slice(1);
    metrics.forEach(m => {
      if (!existing.has(m)) {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = `${m} — ${MACRO_LABELS[m] || m}`;
        optgroup.appendChild(opt);
      }
    });
    if (optgroup.children.length) select.appendChild(optgroup);
  }
  btn.replaceWith(select);
  select.addEventListener('change', () => {
    if (select.value) addIndicator(select.value);
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
  try {
    // Pages 静态部署：预渲染的全指标合并文件，本地按当前组合过滤（一次加载，任意组合可查）
    const res = await fetch('/api/macro/correlate.json');
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const all = await res.json();
    const json = { indicators: {} };
    for (const name of activePreset.indicators) {
      if (all.indicators[name]) json.indicators[name] = all.indicators[name];
    }

    const seriesMap = {};
    for (const [name, info] of Object.entries(json.indicators)) {
      const filtered = applyDateFilter(info.data, dateRange);
      seriesMap[name] = {
        data: filtered.filter(d => d.value != null).map(d => [d.date, d.value]),
        label: info.label,
      };
    }
    renderChart(seriesMap);
  } catch (e) {
    const chartEl = document.getElementById('correlation-chart');
    if (chartEl) chartEl.innerHTML = `<div class="loading">加载失败: ${e.message}</div>`;
  }
}

// ── chart rendering (ECharts, dual y-axis) ─────────────────────────────────
function renderChart(seriesMap) {
  if (corrChart) { corrChart.dispose(); corrChart = null; }
  if (corrObserver) { corrObserver.disconnect(); corrObserver = null; }

  const chartEl = document.getElementById('correlation-chart');
  chartEl.innerHTML = '';
  if (!activePreset) return;

  const needDual = activePreset.left_axis.length > 0 && activePreset.right_axis.length > 0;

  const yAxis = [{ type: 'value', scale: true, splitNumber: 4, name: activePreset.left_axis.join('/') || '' }];
  if (needDual) {
    yAxis.push({ type: 'value', scale: true, splitNumber: 4, name: activePreset.right_axis.join('/') || '' });
  }

  const series = activePreset.indicators.map((name, idx) => {
    const info = seriesMap[name];
    if (!info || !info.data.length) return null;
    const color = MACRO_COLORS[idx % MACRO_COLORS.length];
    const isLeft = activePreset.left_axis.includes(name);
    return {
      name: info.label || name,
      type: 'line',
      data: info.data,
      lineStyle: { color, width: 2 },
      itemStyle: { color },
      showSymbol: false,
      yAxisIndex: (needDual && !isLeft) ? 1 : 0,
      emphasis: { focus: 'series' },
    };
  }).filter(Boolean);

  const chart = echarts.init(chartEl, 'macro', { renderer: 'canvas' });
  corrChart = chart;
  chart.setOption({
    legend: { show: series.length > 1, top: 4, right: 8 },
    grid: { left: '3%', right: needDual ? '8%' : '4%', bottom: 56, top: series.length > 1 ? 40 : 16, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    yAxis,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series,
  });
  corrObserver = new ResizeObserver(() => chart.resize());
  corrObserver.observe(chartEl);
}

// ── indicator management ───────────────────────────────────────────────────
function addIndicator(name) {
  if (!activePreset || activePreset.indicators.includes(name)) return;
  activePreset.indicators = [...activePreset.indicators, name];
  activePreset.right_axis = [...activePreset.right_axis, name];
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
