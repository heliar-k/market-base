// macro-view.js — macro panel with ECharts

import { registerMacroTheme, reThemeECharts } from './echarts-theme.js';
import { MACRO_COLORS, MACRO_LABELS, MACRO_DATE_RANGES, applyDateFilter } from './macro-common.js';

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

// ── 专题速览 tab（当期数字 + 结论 + 跳转专题页；曲线在专题页内查看）────────────────
const FEATURED = [
  {
    id: 'rates', label: '利率', page: '/rates/',
    apis: ['/api/rates/analysis', '/api/fomc/calendar'],
    num: d => `2s10s ${d[0].yield_curve.spreads['2s10s']}bp`,
    concl: d => d[0].overview.sections[0]?.body || '',
    sub: d => `FOMC 目标区间 ${Number(d[1].target_lower).toFixed(2)}% – ${Number(d[1].target_upper).toFixed(2)}%`,
  },
  {
    id: 'fed', label: '美联储', page: '/fed/',
    apis: ['/api/fed/overview'],
    num: d => `鹰鸽 ${d[0].indicator.label}`,
    concl: d => `官员立场样本 ${d[0].indicator.sample} 人`,
  },
  {
    id: 'credit', label: '信用', page: '/credit/',
    apis: ['/api/credit/overview'],
    num: d => `信用 ${d[0].regime.regime}`,
    concl: d => `HY-IG 利差 ${d[0].hy_ig.value}bp · ${d[0].hy_ig.as_of}`,
  },
  {
    id: 'vol', label: '波动率', page: '/volatility/',
    apis: ['/api/volatility/analysis'],
    num: d => `VIX ${d[0].vix.value}`,
    concl: d => `${d[0].vix.zone}区间 · ${d[0].vix.percentile_1y}% 分位`,
    sub: d => d[0].signals[0]?.title || '',
  },
];

async function renderFeatureTabs(container) {
  const wrap = document.createElement('div');
  wrap.className = 'macro-feature';
  const tabs = document.createElement('div');
  tabs.className = 'macro-feature-tabs';
  const panels = document.createElement('div');
  panels.className = 'macro-feature-panels';

  const results = await Promise.all(FEATURED.map(async f => {
    try {
      const datas = await Promise.all(f.apis.map(u => fetch(u).then(r => r.json())));
      return { f, datas, err: null };
    } catch (e) {
      return { f, datas: null, err: e };
    }
  }));

  results.forEach(({ f, datas, err }, i) => {
    const btn = document.createElement('button');
    btn.className = 'macro-feature-tab' + (i === 0 ? ' active' : '');
    btn.textContent = f.label;
    btn.addEventListener('click', () => {
      panels.querySelectorAll('.macro-feature-panel').forEach(p =>
        p.classList.toggle('active', p.dataset.fid === f.id));
      tabs.querySelectorAll('.macro-feature-tab').forEach(t =>
        t.classList.toggle('active', t === btn));
    });
    tabs.appendChild(btn);

    const panel = document.createElement('div');
    panel.className = 'macro-feature-panel' + (i === 0 ? ' active' : '');
    panel.dataset.fid = f.id;
    if (err || !datas) {
      panel.innerHTML = '<div class="loading">加载失败</div>';
    } else {
      panel.innerHTML = `
        <div class="macro-feature-num">${f.num(datas)}</div>
        <div class="macro-feature-concl">${f.concl(datas)}</div>
        ${f.sub ? `<div class="macro-feature-sub">${f.sub(datas)}</div>` : ''}
        <a class="macro-feature-link" href="${f.page}">查看详情 →</a>`;
    }
    panels.appendChild(panel);
  });

  wrap.appendChild(tabs);
  wrap.appendChild(panels);
  container.appendChild(wrap);
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

  renderFeatureTabs(container);

  const grid = document.createElement('div');
  grid.className = 'macro-grid';

  // 有专题页的分类（rates/credit/volatility）不在宏观页重复画曲线，见专题速览 tab
  const featuredCats = new Set(['rates', 'credit', 'volatility']);
  macroCategories.filter(cat => !featuredCats.has(cat.name)).forEach(cat => {
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

  // 按最新值量级分组：组内跨度 ≤20 倍，避免存量（万亿）与流量（十亿）量纲悬殊
  // 同图导致小序列被压扁（贪心：排序后逐元素归组，超出倍数开新组）
  const last = data[data.length - 1];
  const kv = activeKeys
    .map(k => [k, last ? last[k] : null])
    .filter(p => p[1] != null)
    .sort((a, b) => Math.abs(a[1]) - Math.abs(b[1]));
  const groups = [];
  for (const p of kv) {
    const g = groups[groups.length - 1];
    if (g) {
      const lo = Math.min(...g.map(x => Math.abs(x[1])));
      const hi = Math.max(...g.map(x => Math.abs(x[1])));
      const v = Math.abs(p[1]);
      if (hi / Math.max(lo, 1e-9) > 20 || v / Math.max(lo, 1e-9) > 20 || hi / Math.max(v, 1e-9) > 20) {
        groups.push([p]);
      } else {
        g.push(p);
      }
    } else {
      groups.push([p]);
    }
  }

  let gi = 0;
  for (const group of groups) {
    gi++;
    const keys = group.map(p => p[0]);
    const key = groups.length > 1 ? `${name}-g${gi}` : name;
    const chartWrap = document.createElement('div');
    chartWrap.className = 'macro-series-chart';
    chartWrap.id = `macro-ec-${key}`;
    chartWrap.style.cssText = 'height:360px;width:100%';
    container.appendChild(chartWrap);

    const series = keys.map((k, idx) => ({
      name: MACRO_LABELS[k] || k,
      type: 'line',
      // 过滤 null：月频/季频序列（AAA/SLOOS 等）只画有效点，避免日频轴上断点
      data: data.filter(d => d[k] != null).map(d => [d.date, d[k]]),
      lineStyle: { width: 1.5 },
      itemStyle: { color: MACRO_COLORS[idx % MACRO_COLORS.length] },
      showSymbol: false,
      emphasis: { focus: 'series' },
    }));

    const chart = echarts.init(chartWrap, 'macro', { renderer: 'canvas' });
    chart.setOption({
      legend: { show: keys.length > 1, top: 4, right: 8 },
      grid: { left: '3%', right: '4%', bottom: 56, top: keys.length > 1 ? 40 : 16, containLabel: true },
      xAxis: { type: 'time', boundaryGap: false },
      yAxis: { type: 'value', scale: true, splitNumber: 4 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }],
      series,
    });

    macroChartInstances[key] = { chart, observer: observe(chart, chartWrap) };
  }
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
