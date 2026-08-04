// macro-view.js — macro panel with ECharts

import { registerMacroTheme, reThemeECharts } from './echarts-theme.js';
import { MACRO_COLORS, MACRO_LABELS, MACRO_DATE_RANGES, applyDateFilter } from './macro-common.js';

// ── corridor chart config ───────────────────────────────────────────────────
// EFFR 用 DFF（日频）；FEDFUNDS 为月频均值，画在日频图上会错位/滞后（审计 P1-②）
const CORRIDOR_KEYS = ['DFF', 'DFEDTARL', 'DFEDTARU', 'SOFR', 'OBFR', 'IORB'];
const CORRIDOR_COLORS = {
  DFEDTARL: '#ef5350', DFEDTARU: '#ef5350',
  DFF: '#1a73e8', SOFR: '#ff9800', OBFR: '#4caf50', IORB: '#9c27b0',
};

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

function observe(chart, el) {
  const observer = new ResizeObserver(() => chart.resize());
  observer.observe(el);
  return observer;
}

// ── init ───────────────────────────────────────────────────────────────────
export function initMacroView() {
  registerMacroTheme();
  loadMacroCategories();
  window.addEventListener('theme-changed', onThemeChanged);
}

function onThemeChanged() {
  Object.entries(macroChartInstances).forEach(([name, entry]) => {
    const dom = entry.chart.getDom();
    const opts = entry.chart.getOption();
    entry.observer.disconnect();
    const next = reThemeECharts(entry.chart, dom, opts);
    macroChartInstances[name] = { chart: next, observer: observe(next, dom) };
  });
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

  // ── 利率走廊专用区块（在分类列表上方）──
  const corridorSection = document.createElement('div');
  corridorSection.className = 'macro-section collapsed';
  corridorSection.innerHTML = `<div class="macro-section-header"><span class="macro-expand">▶</span><span class="macro-cat-name">利率走廊</span><span class="macro-cat-count">Fed Funds Corridor</span></div><div class="macro-section-body"><div class="macro-series-charts" id="macro-charts-corridor"></div></div>`;
  corridorSection.querySelector('.macro-section-header').addEventListener('click', () => toggleCorridorSection(corridorSection));
  grid.appendChild(corridorSection);

  macroCategories.forEach(cat => {
    const section = document.createElement('div');
    section.className = 'macro-section collapsed';
    section.innerHTML = `<div class="macro-section-header" data-cat="${cat.name}"><span class="macro-expand">▶</span><span class="macro-cat-name">${catNameLabel(cat.name)}</span><span class="macro-cat-count">${cat.series.length} 项</span></div><div class="macro-section-body"><div class="macro-series-charts" id="macro-charts-${cat.name}"></div></div>`;
    section.querySelector('.macro-section-header').addEventListener('click', () => toggleMacroSection(cat.name, cat.series, section));
    grid.appendChild(section);
  });
  container.appendChild(grid);
  // 首次加载自动展开利率走廊
  setTimeout(() => toggleCorridorSection(corridorSection), 50);
}

function catNameLabel(name) {
  if (name === 'fx') return 'FX';
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// ── corridor section ─────────────────────────────────────────────────────────

let corridorCache = null;

function initCorridorChart(el, title, yAxisOpts, series) {
  const ec = echarts.init(el, 'macro', { renderer: 'canvas' });
  const opts = {
    title: { text: title, left: 12, top: 8, textStyle: { fontSize: 13, fontWeight: 'bold' } },
    legend: { show: true, top: 4, right: 8, textStyle: { fontSize: 10 } },
    grid: { left: '3%', right: '4%', bottom: 48, top: 52, containLabel: true },
    xAxis: { type: 'time', boundaryGap: false },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
    series,
  };
  if (Array.isArray(yAxisOpts)) {
    opts.yAxis = yAxisOpts;
  } else {
    opts.yAxis = { type: 'value', scale: true, ...yAxisOpts };
  }
  ec.setOption(opts);
  return ec;
}

async function toggleCorridorSection(section) {
  const isOpen = section.classList.contains('open');
  if (isOpen) {
    section.classList.remove('open');
    section.classList.add('collapsed');
    return;
  }
  section.classList.remove('collapsed');
  section.classList.add('open');

  const container = document.getElementById('macro-charts-corridor');
  if (container.children.length > 0) return;

  if (!corridorCache) {
    const [fcRes, ratesRes] = await Promise.all([
      fetch('/api/fomc/calendar'),
      fetch('/api/macro/rates'),
    ]);
    corridorCache = {
      fomc: await fcRes.json(),
      rates: applyDateFilter(await ratesRes.json(), macroDateRange),
    };
  }

  renderCorridorDashboard(container);
}

function renderCorridorDashboard(container) {
  const { fomc, rates } = corridorCache;
  if (!rates || rates.length === 0) return;

  const targetLower = fomc.target_lower ?? '—';
  const targetUpper = fomc.target_upper ?? '—';
  const targetStr = targetLower !== '—' ? `${targetLower.toFixed(2)}% – ${targetUpper.toFixed(2)}%` : '—';

  // ── 目标区间卡片（数据来自 FOMC API）──
  const card = document.createElement('div');
  card.className = 'corridor-target-card';
  card.innerHTML = `
    <div class="corridor-target-range">
      <span class="corridor-target-label">FOMC 目标区间</span>
      <span class="corridor-target-value">${targetStr}</span>
    </div>
    <div class="corridor-next-meeting">
      <span class="corridor-next-label">下次会议</span>
      <span class="corridor-next-value">${fomc.next ? `${fomc.next.year}-${String(fomc.next.month).padStart(2,'0')}-${String(fomc.next.start_day).padStart(2,'0')} / ${String(fomc.next.end_day).padStart(2,'0')}` : '—'}</span>
    </div>
    <div style="margin-left:auto;display:flex;align-items:flex-end;gap:12px">
      <a href="/fed/" style="color:#58a6ff;font-size:13px;text-decoration:none;white-space:nowrap">美联储 →</a>
      <a href="/rates/" style="color:#58a6ff;font-size:13px;text-decoration:none;white-space:nowrap">利率专题 →</a>
      <a href="/volatility/" style="color:#58a6ff;font-size:13px;text-decoration:none;white-space:nowrap">波动率 →</a>
    </div>
  `;
  container.appendChild(card);

  // ── 短期利率走廊图 ──
  const ch1 = appendChart(container, 340);
  const ec1 = initCorridorChart(ch1, '短期利率走廊',
    { axisLabel: { formatter: '{value}%' } },
    CORRIDOR_KEYS.map(key => ({
      name: MACRO_LABELS[key] || key,
      type: 'line',
      data: rates.map(d => [d.date, d[key]]),
      lineStyle: { width: key === 'DFF' ? 2 : 1.2, type: key === 'DFEDTARL' || key === 'DFEDTARU' ? 'dashed' : 'solid' },
      itemStyle: { color: CORRIDOR_COLORS[key] || MACRO_COLORS[0] },
      showSymbol: false, emphasis: { focus: 'series' },
    }))
  );
  macroChartInstances['corridor-main'] = { chart: ec1, observer: observe(ec1, ch1) };

  // ── 联邦基金有效利率（5 年，DFF 日频）──
  const ch2 = appendChart(container, 280);
  const ec2 = initCorridorChart(ch2, '联邦基金有效利率',
    { axisLabel: { formatter: '{value}%' } },
    [{
      name: MACRO_LABELS['DFF'] || 'DFF',
      type: 'line',
      data: rates.map(d => [d.date, d.DFF]),
      lineStyle: { width: 2, color: '#1a73e8' },
      itemStyle: { color: '#1a73e8' },
      showSymbol: false, emphasis: { focus: 'series' },
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { type: 'dashed', color: '#ef5350', width: 1 },
        data: [{ yAxis: fomc.target_lower, label: { formatter: '下限 {c}%', fontSize: 10 } },
               { yAxis: fomc.target_upper, label: { formatter: '上限 {c}%', fontSize: 10 } }],
      },
    }]
  );
  macroChartInstances['corridor-fedfunds'] = { chart: ec2, observer: observe(ec2, ch2) };

  // ── SOFR 分位数走廊图 ──
  const ch3 = appendChart(container, 300);
  const ec3 = initCorridorChart(ch3, 'SOFR 利率走廊（分位数）',
    { axisLabel: { formatter: '{value}%' } },
    [
      { name: 'SOFR 99th', type: 'line', data: rates.map(d => [d.date, d.SOFR99]), lineStyle: { width: 0.5, color: '#546e7a' }, showSymbol: false, emphasis: { focus: 'series' } },
      { name: 'SOFR 75th', type: 'line', data: rates.map(d => [d.date, d.SOFR75]), lineStyle: { width: 0.5, color: '#78909c' }, showSymbol: false, emphasis: { focus: 'series' }, areaStyle: { color: 'rgba(144,164,174,0.06)' } },
      { name: 'SOFR', type: 'line', data: rates.map(d => [d.date, d.SOFR]), lineStyle: { width: 1.8, color: '#ff9800' }, showSymbol: false, emphasis: { focus: 'series' } },
      { name: 'SOFR 25th', type: 'line', data: rates.map(d => [d.date, d.SOFR25]), lineStyle: { width: 0.5, color: '#78909c' }, showSymbol: false, emphasis: { focus: 'series' }, areaStyle: { color: 'rgba(144,164,174,0.06)' } },
      { name: 'SOFR 1st', type: 'line', data: rates.map(d => [d.date, d.SOFR1]), lineStyle: { width: 0.5, color: '#546e7a' }, showSymbol: false, emphasis: { focus: 'series' } },
    ]
  );
  macroChartInstances['corridor-sofr-pctl'] = { chart: ec3, observer: observe(ec3, ch3) };

  // ── SOFR 成交量（柱状图）──
  const ch4 = appendChart(container, 260);
  const ec4 = initCorridorChart(ch4, 'SOFR 每日成交量',
    [{ type: 'value', name: '十亿美元', scale: true }],
    [
      { name: 'SOFR Volume', type: 'bar', data: rates.map(d => [d.date, d.SOFRVOL]), itemStyle: { color: 'rgba(26,115,232,0.3)' } },
    ]
  );
  macroChartInstances['corridor-sofr-vol'] = { chart: ec4, observer: observe(ec4, ch4) };

  // ── 近期数据表 ──
  const table = document.createElement('div');
  table.className = 'corridor-table-wrap';
  table.innerHTML = buildCorridorTable(rates);
  container.appendChild(table);
}

function appendChart(container, height) {
  const el = document.createElement('div');
  el.className = 'corridor-chart';
  el.style.cssText = `height:${height}px;width:100%`;
  container.appendChild(el);
  return el;
}

// ── 近期数据表 ──
function buildCorridorTable(data) {
  const rows = data.slice(-30).reverse();
  const header = ['日期', 'EFFR(日)', '下限', '上限', 'SOFR', 'OBFR', 'IORB', '成交量(B)'];
  const keys = ['date', 'DFF', 'DFEDTARL', 'DFEDTARU', 'SOFR', 'OBFR', 'IORB', 'SOFRVOL'];
  const thead = `<tr>${header.map(h => `<th>${h}</th>`).join('')}</tr>`;
  const tbody = rows.map(r =>
    `<tr>${keys.map(k => `<td>${r[k] != null ? (k === 'date' ? r[k] : typeof r[k] === 'number' ? r[k].toFixed(2) : r[k]) : '—'}</td>`).join('')}</tr>`
  ).join('');
  return `<table class="corridor-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
}

// ── corridor section end ────────────────────────────────────────────────────

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

  // 保存 fomc 引用再清 corridor cache，避免日期切换丢失会议数据
  const savedFomc = corridorCache?.fomc;
  corridorCache = null;

  // 走廊 section 若已展开则仅 dispose 走廊图表，不动其他 section
  const corridorChartsEl = document.getElementById('macro-charts-corridor');
  if (corridorChartsEl) {
    const corrSection = corridorChartsEl.closest('.macro-section');
    if (corrSection && corrSection.classList.contains('open')) {
      ['corridor-main', 'corridor-fedfunds', 'corridor-sofr-pctl', 'corridor-sofr-vol'].forEach(disposeChart);
      corridorChartsEl.innerHTML = '';
      fetch('/api/macro/rates').then(r => r.json()).then(data => {
        corridorCache = { fomc: savedFomc, rates: applyDateFilter(data, macroDateRange) };
        renderCorridorDashboard(corridorChartsEl);
      });
    }
  }

  // 仅重渲染已展开的分类 section；折叠的不动
  const openNames = new Set();
  macroCategories.forEach(cat => {
    const sections = document.querySelectorAll(`.macro-section-header[data-cat="${cat.name}"]`);
    sections.forEach(header => {
      const section = header.closest('.macro-section');
      if (section && section.classList.contains('open')) {
        openNames.add(cat.name);
        disposeChart(cat.name);
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

  // 清掉折叠 section 的缓存，下次展开时重新拉取；已展开的保留
  macroCache = Object.fromEntries(
    Object.entries(macroCache).filter(([k]) => openNames.has(k))
  );
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = macroCategories ? `${macroCategories.length} 分类` : '';
  document.getElementById('status-range').textContent = '';
}
