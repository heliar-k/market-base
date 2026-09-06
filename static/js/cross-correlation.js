// cross-correlation.js — 关联分析面板（双模式：资产相关 / 宏观联动）
//
// 资产相关模式（默认）：跨资产 30 日相关矩阵 + 结构状态条 + 结构洞察
//   - 结构层：风险偏好/股票 dispersion/防御资产/利率波动 四状态卡（先看结构）
//   - 数据层：分组建模热力图（点 cell → 滚动相关 drill-down，再看数据）
//   - 洞察层：消费 /api/cross-asset alerts 的规则引擎叙事
// 宏观联动模式：保留原有 FRED 指标 overlay（预设 + 自由加指标 + 日期范围）
//
// UI 原则见 AGENTS.md「主站 Web UI/UX 设计原则」。

import { registerMacroTheme } from './echarts-theme.js';
import { MACRO_COLORS, MACRO_LABELS, MACRO_DATE_RANGES, applyDateFilter } from './macro-common.js';

// ── constants ──────────────────────────────────────────────────────────────
const GROUP_ORDER = ['equity', 'bond', 'credit', 'commodity', 'crypto', 'fx', 'vol'];
const GROUP_LABELS = {
  equity: '美股', bond: '国债', credit: '信用', commodity: '商品',
  crypto: '加密', fx: '美元/FX', vol: '波动率',
};
const GROUP_COLORS = {
  equity: '#1a73e8', bond: '#9c27b0', credit: '#7c4dff', commodity: '#ff9800',
  crypto: '#26a69a', fx: '#00bcd4', vol: '#ef5350',
};
// drill-down 滚动相关参数（与 cross_asset.py 同口径；前端重算避免每对导一个静态文件）
const ROLLING_WINDOW = 30;
const MIN_OBS = 10;
// 状态卡定义：key → { label, desc, pairs, dir }；dir = 正值含义
// 「好坏」是结构判断不是涨跌判断：结构恶化 = 风险偏好瓦解 / 对冲失效
const STATUS_CARDS = [
  {
    key: 'risk_appetite', label: '风险偏好', desc: '股-加密 / 股-信用 同向性',
    pairs: ['SPX_BTC_30d', 'SPX_HYG_30d'], dir: 'risk_on',
  },
  {
    key: 'equity_dispersion', label: '股票 dispersion', desc: '美股内部相关性（低 = 个股行情）',
    pairs: ['SPX_NDX_30d', 'SPX_RUT_30d'], dir: 'avg',
  },
  {
    key: 'defensive_assets', label: '防御资产', desc: '股债 / 股金对冲有效性（越负越有效）',
    pairs: ['SPX_TLT_30d', 'SPX_Gold_30d'], dir: 'negative_good',
  },
  {
    key: 'rate_vol', label: '利率波动', desc: 'MOVE×股 / MOVE×债 传染度',
    pairs: ['MOVE_SPX_30d', 'MOVE_TLT_30d'], dir: 'stress',
  },
];

// 报警对叙事（规则引擎）：level → 标题/正文；与 assets 页报警卡口径一致
const ALERT_RULES = {
  SPX_TLT_30d: {
    label: '股债相关性',
    zones: [
      { min: 0.3, title: '股债同涨同跌 — 60/40 分散化失效', text: '通胀/利率是当前主导驱动，债券失去对冲属性，股票回撤时久期不再保护组合。' },
      { min: 0, title: '股债正相关 — 对冲属性减弱', text: '长债与股票同向，传统 60/40 的分散化效果打折扣，关注组合久期暴露。' },
      { min: -0.3, title: '弱负相关 — 对冲属性中性', text: '股债关系处于过渡区，分散化效果一般。' },
      { min: -1.01, title: '股债负相关 — 对冲属性回归', text: '增长担忧主导定价，长债在股票下跌时提供保护，经典股债跷跷板。' },
    ],
  },
  WTI_SPX_30d: {
    label: '油股相关性',
    zones: [
      { min: 0.3, title: '油股同涨 — 需求驱动', text: '油价与股市同步走强，扩张期需求叙事主导。' },
      { min: -0.3, title: '油股脱钩', text: '油价与股市相关性弱，能源波动暂未传导至风险偏好。' },
      { min: -1.01, title: '油股深负相关 — 滞胀/供给冲击', text: '油价上行而股票承压，供给冲击或滞胀叙事主导，防御结构优先。' },
    ],
  },
  DXY_HYG_30d: {
    label: '美元-信用',
    zones: [
      { min: 0.3, title: '美元与信用同强 — 避险但分化', text: '美元走强伴随高收益债走强，信用利差收紧主导，金融条件未恶化。' },
      { min: -0.3, title: '美元-信用中性', text: '美元与信用资产相关性弱。' },
      { min: -1.01, title: '美元强 + 信用走弱 — 金融条件收紧', text: '美元升、高收益债跌的组合是经典收紧信号，警惕信用利差走阔向股市传导。' },
    ],
  },
  MOVE_SPX_30d: {
    label: '债市波动-股票',
    zones: [
      { min: 0.3, title: 'MOVE 与股票同向 — 久期对冲失效', text: '债市波动上升伴随股票上涨（或同跌），久期不再是对冲工具，跨资产波动传染。' },
      { min: -0.3, title: 'MOVE 与股票弱相关', text: '债市波动未显著影响股票定价。' },
      { min: -1.01, title: 'MOVE 与股票负相关 — 波动正常传导', text: '债市波动上升伴随股票下跌，久期资产波动率主导风险偏好，属常态传导。' },
    ],
  },
};

// ── state ──────────────────────────────────────────────────────────────────
let mode = 'asset'; // 'asset' | 'macro'
let presets = null;
let activePreset = null;
let crossData = null; // /api/cross-asset payload
let matrixChart = null;
let drillChart = null;
let corrChart = null;
let corrObserver = null;
let dateRange = '5y'; // 默认 5 年：78 年全量下近期联动不可读，All 保留可选
let seriesMapCache = null; // 宏观模式最近一次渲染的 seriesMap（状态栏数据截至用）

// ── init ───────────────────────────────────────────────────────────────────
export async function initCorrelationView() {
  registerMacroTheme();
  window.addEventListener('theme-changed', onThemeChanged);
  // 宏观联动模式的依赖照旧加载
  try {
    const res = await fetch('/api/macro/presets');
    const json = await res.json();
    presets = json.presets;
  } catch {
    presets = [];
  }
  renderUI();
  if (mode === 'asset') loadCrossAsset();
  else if (presets.length) loadPreset(presets[0]);
}

function onThemeChanged() {
  // 直接按当前模式重渲染（比逐图 re-init 简单，insight spark 一并重建）
  if (mode !== 'asset') { if (corrChart) loadAndRender(); return; }
  if (!crossData) return;
  renderStatusRow(crossData);
  renderMatrix();
  renderInsights(crossData);
}

// ── UI scaffold ────────────────────────────────────────────────────────────
function renderUI() {
  const card = document.getElementById('correlation-card');
  card.innerHTML = '';

  // 顶部工具栏：模式切换（左）+ 日期范围（右，宏观模式用）
  const toolbar = document.createElement('div');
  toolbar.className = 'corr-toolbar';
  toolbar.innerHTML = `
    <div class="range-controls" style="margin-left:0">
      <button class="range-btn${mode === 'asset' ? ' active' : ''}" data-mode="asset">资产相关</button>
      <button class="range-btn${mode === 'macro' ? ' active' : ''}" data-mode="macro">宏观联动</button>
    </div>
    <div class="correlation-presets" id="corr-presets" style="padding:0;border:none;flex:1"></div>
    <div class="correlation-date-toolbar" id="corr-date-toolbar" style="display:none"></div>`;
  toolbar.querySelectorAll('[data-mode]').forEach(btn => {
    btn.addEventListener('click', () => switchMode(btn.dataset.mode));
  });
  card.appendChild(toolbar);

  // 资产相关模式容器
  const assetWrap = document.createElement('div');
  assetWrap.className = 'corr-asset-wrap';
  assetWrap.id = 'corr-asset-wrap';
  assetWrap.style.display = mode === 'asset' ? 'flex' : 'none';
  card.appendChild(assetWrap);

  // 宏观联动模式容器（保留原有结构）
  const macroWrap = document.createElement('div');
  macroWrap.className = 'corr-macro-wrap';
  macroWrap.id = 'corr-macro-wrap';
  macroWrap.style.display = mode === 'macro' ? 'flex' : 'none';
  card.appendChild(macroWrap);

  if (mode === 'asset') renderAssetSkeleton(assetWrap);
  else renderMacroSkeleton(macroWrap);
}

function switchMode(next) {
  if (mode === next) return;
  mode = next;
  // 释放不活跃模式的图实例
  if (mode === 'asset') { if (corrChart) { corrChart.dispose(); corrChart = null; } }
  else { disposeMatrixCharts(); }
  renderUI();
  if (mode === 'asset') loadCrossAsset();
  else if (presets.length) loadPreset(presets[0]);
}

// ── 资产相关模式 ────────────────────────────────────────────────────────────
function renderAssetSkeleton(wrap) {
  wrap.innerHTML = `
    <div class="corr-status-row" id="corr-status-row"><div class="loading" style="height:auto;padding:24px">加载中…</div></div>
    <div class="corr-main">
      <div class="corr-matrix-pane">
        <div class="corr-pane-head">
          <span class="corr-pane-title">跨资产相关性矩阵</span>
          <span class="corr-pane-sub" id="corr-matrix-sub"></span>
        </div>
        <div id="corr-heatmap" class="corr-heatmap"></div>
        <div class="corr-group-legend" id="corr-group-legend"></div>
      </div>
      <div class="corr-side-pane">
        <div class="corr-pane-head"><span class="corr-pane-title">结构洞察</span></div>
        <div id="corr-insights"><div class="loading" style="height:auto;padding:24px">加载中…</div></div>
      </div>
    </div>`;
}

async function loadCrossAsset() {
  try {
    const res = await fetch('/api/cross-asset');
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    crossData = await res.json();
    renderStatusRow(crossData);
    renderMatrix();
    renderInsights(crossData);
    updateStatus();
  } catch (e) {
    const row = document.getElementById('corr-status-row');
    if (row) row.innerHTML = `<div class="loading" style="height:auto;padding:24px">加载失败: ${e.message}</div>`;
  }
}

// 状态条：四张结构卡（先看结构）
function renderStatusRow(d) {
  const row = document.getElementById('corr-status-row');
  if (!row) return;
  row.innerHTML = '';
  const lookup = {};
  for (const a of d.alerts) lookup[a.key] = a;
  // 矩阵里取任意对的快捷函数
  const names = d.assets.map(a => a.name);
  const pair = (i, j) => {
    const r = names.indexOf(i), c = names.indexOf(j);
    return r >= 0 && c >= 0 ? d.matrix[r][c] : null;
  };
  for (const spec of STATUS_CARDS) {
    const vals = spec.pairs
      .map(p => {
        const [i, j, win] = p.split('_');
        return win ? lookup[p]?.latest ?? pair(i, j) : pair(i, j);
      })
      .filter(v => v != null);
    const avg = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    const card = document.createElement('div');
    card.className = 'dash-stat corr-status-card';
    const levelText = avg == null ? '—' : levelLabel(spec.dir, avg);
    card.innerHTML = `
      <div class="dash-stat-label">${spec.label}</div>
      <div class="dash-stat-value ${avg == null ? '' : levelClass(spec.dir, avg)}">${avg == null ? '—' : avg >= 0 ? '+' : ''}${avg.toFixed(2)}</div>
      <div class="dash-stat-desc">${spec.desc}</div>
      <div class="dash-stat-change">${levelText}${vals.length < spec.pairs.length ? ' · 部分序列缺数据' : ''}</div>`;
    card.addEventListener('click', () => {
      const sub = document.getElementById('corr-matrix-sub');
      if (sub) sub.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    row.appendChild(card);
  }
}

function levelClass(dir, v) {
  if (dir === 'negative_good') return v < 0 ? 'up' : 'down';      // up=绿（健康）
  if (dir === 'stress') return v > 0.5 ? 'down' : v > 0 ? 'neutral' : 'up';
  if (dir === 'risk_on') return v > 0.3 ? 'up' : v < -0.3 ? 'down' : 'neutral';
  return 'neutral';
}
function levelLabel(dir, v) {
  if (dir === 'negative_good') return v < -0.3 ? '对冲有效' : v < 0 ? '对冲偏有效' : v < 0.3 ? '对冲减弱' : '对冲失效';
  if (dir === 'stress') return v > 0.5 ? '高传染' : v > 0 ? '有传染' : '低传染';
  if (dir === 'risk_on') return v > 0.3 ? 'risk-on' : v < -0.3 ? 'risk-off' : '中性';
  return v > 0.6 ? '同涨同跌' : v < 0.2 ? '个股行情' : '温和分化';
}

// 热力图：分组建模 + 点击 cell drill-down
function renderMatrix() {
  const d = crossData;
  const sub = document.getElementById('corr-matrix-sub');
  if (sub) sub.textContent = `数据截至 ${d.as_of} · ${d.window} 日滚动 · 点格子看滚动相关`;
  const assets = d.assets.map(a => a.name);
  const labels = d.assets.map(a => a.label);

  const dom = document.getElementById('corr-heatmap');
  if (matrixChart) { matrixChart.dispose(); matrixChart = null; }
  matrixChart = echarts.init(dom, document.body.classList.contains('dark') ? 'macroDark' : 'macro', { renderer: 'canvas' });

  const dark = document.body.classList.contains('dark');
  const data = [];
  d.matrix.forEach((row, i) => row.forEach((v, j) => data.push([j, i, v])));

  matrixChart.setOption({
    tooltip: {
      position: 'top',
      formatter: p => {
        const v = p.value && p.value[2];
        return `${labels[p.value[1]]} × ${labels[p.value[0]]}<br>${v == null ? '—' : Number(v).toFixed(2)}`;
      },
    },
    grid: { left: 8, right: 16, top: 8, bottom: 90, containLabel: true },
    xAxis: {
      type: 'category', data: assets, position: 'top',
      axisLabel: { color: '#737373', fontSize: 10, rotate: 50, interval: 0 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    yAxis: {
      type: 'category', data: assets,
      axisLabel: { color: '#737373', fontSize: 10 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    visualMap: {
      min: -1, max: 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 4,
      inRange: {
        color: dark
          ? ['#60a5fa', '#1e3a5f', '#1f2937', '#7f1d1d', '#f87171']
          : ['#1d4ed8', '#93c5fd', '#f1f5f9', '#fca5a5', '#dc2626'],
      },
      text: ['强正相关', '强负相关'],
      formatter: () => '',
      textStyle: { color: '#737373', fontSize: 11 },
    },
    series: [{
      type: 'heatmap', data,
      itemStyle: { borderColor: 'transparent', borderWidth: 1 },
      emphasis: { itemStyle: { borderColor: '#1a73e8', borderWidth: 2 } },
      label: {
        show: true, fontSize: 9,
        rich: dark
          ? { s: { color: '#1f2937', fontSize: 9 }, n: { color: '#8b949e', fontSize: 9 } }
          : { s: { color: '#fff', fontSize: 9 }, n: { color: '#334155', fontSize: 9 } },
        formatter: p => {
          const v = p.value && p.value[2];
          if (v == null) return '';
          return `{${Math.abs(v) >= 0.75 ? 's' : 'n'}|${v.toFixed(2)}}`;
        },
      },
    }],
  });
  hookMatrixEvents(matrixChart);

  // 图例行
  const legend = document.getElementById('corr-group-legend');
  if (legend) {
    legend.innerHTML = GROUP_ORDER
      .filter(g => d.assets.some(a => a.group === g))
      .map(g => `<span class="corr-group-item"><i style="background:${GROUP_COLORS[g]}"></i>${GROUP_LABELS[g]}</span>`)
      .join('');
  }
}

function hookMatrixEvents(chart) {
  chart.off('click');
  chart.on('click', p => {
    if (!p.value) return;
    const a = crossData.assets[p.value[0]].name;
    const b = crossData.assets[p.value[1]].name;
    if (a === b) return;
    openDrilldown(a, b);
  });
}

// drill-down 弹层：两标的滚动相关 + 各自价格归一化
let pricesCache = null; // /api/assets/prices 全量宽表（首次点击后缓存，静态站也可用）
async function openDrilldown(a, b) {
  disposeMatrixCharts(); // 释放上一次 drill-down 的图实例
  const overlay = document.createElement('div');
  overlay.className = 'corr-drill-overlay';
  overlay.innerHTML = `
    <div class="corr-drill-panel chart-card">
      <div class="corr-pane-head">
        <span class="corr-pane-title">${labelOf(a)} × ${labelOf(b)} 滚动相关</span>
        <button class="corr-drill-close">×</button>
      </div>
      <div class="corr-drill-charts">
        <div id="corr-drill-corr" style="flex:1;min-height:180px"></div>
        <div id="corr-drill-price" style="flex:1;min-height:180px"></div>
      </div>
      <div class="corr-drill-note">上：${ROLLING_WINDOW} 日滚动相关系数；下：两标的归一化价格（期初 = 100）。数据源 data/yfinance/asset_prices.csv</div>
    </div>`;
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('.corr-drill-close').addEventListener('click', () => overlay.remove());
  document.getElementById('corr-asset-wrap').appendChild(overlay);

  try {
    if (!pricesCache) {
      const res = await fetch('/api/assets/prices');
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      pricesCache = await res.json();
    }
    renderDrillCharts(a, b, pricesCache.prices);
  } catch {
    overlay.querySelector('.corr-drill-note').textContent = '价格序列加载失败（数据源 asset_prices.csv 缺失）';
  }
}

function labelOf(name) {
  const a = crossData.assets.find(x => x.name === name);
  return a ? a.label : name;
}

function renderDrillCharts(a, b, rows) {
  const dark = document.body.classList.contains('dark');
  const theme = dark ? 'macroDark' : 'macro';
  const dates = rows.map(r => r.date);
  const norm = (name) => {
    let base = null;
    return rows.map(r => {
      const v = r[name];
      if (v == null) return null;
      if (base == null) base = v;
      return +(v / base * 100).toFixed(2);
    });
  };

  // 滚动相关（与 cross_asset.py 同口径：pct_change + rolling 30，min 10 有效观测）
  const ra = rows.map(r => r[a]), rb = rows.map(r => r[b]);
  const corrSeries = [];
  for (let k = ROLLING_WINDOW; k < rows.length; k++) {
    const sa = [], sb = [];
    for (let m = k - ROLLING_WINDOW + 1; m <= k; m++) {
      if (ra[m] != null && ra[m - 1] != null && rb[m] != null && rb[m - 1] != null) {
        sa.push(ra[m] / ra[m - 1] - 1); sb.push(rb[m] / rb[m - 1] - 1);
      }
    }
    if (sa.length < MIN_OBS) { corrSeries.push(null); continue; }
    corrSeries.push(pearson(sa, sb));
  }

  const cDom = document.getElementById('corr-drill-corr');
  drillChart = echarts.init(cDom, theme, { renderer: 'canvas' });
  drillChart.setOption({
    grid: { left: 8, right: 16, top: 12, bottom: 40, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', min: -1, max: 1 },
    visualMap: {
      show: false, min: -1, max: 1,
      inRange: { color: dark ? ['#60a5fa', '#f87171'] : ['#1d4ed8', '#dc2626'] },
    },
    series: [{
      type: 'line', data: corrSeries, showSymbol: false, connectNulls: true,
      lineStyle: { width: 1.5 }, areaStyle: { opacity: 0.08 },
    }],
    tooltip: { trigger: 'axis' },
  });

  const pDom = document.getElementById('corr-drill-price');
  const pChart = echarts.init(pDom, theme, { renderer: 'canvas' });
  pChart.setOption({
    grid: { left: 8, right: 16, top: 12, bottom: 40, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', scale: true },
    series: [
      { name: labelOf(a), type: 'line', data: norm(a), showSymbol: false, lineStyle: { width: 1.5 }, color: MACRO_COLORS[0] },
      { name: labelOf(b), type: 'line', data: norm(b), showSymbol: false, lineStyle: { width: 1.5 }, color: MACRO_COLORS[1] },
    ],
    legend: { top: 0, right: 8 },
    tooltip: { trigger: 'axis' },
  });
}

function pearson(xs, ys) {
  const n = xs.length;
  const mx = xs.reduce((s, v) => s + v, 0) / n;
  const my = ys.reduce((s, v) => s + v, 0) / n;
  let cov = 0, vx = 0, vy = 0;
  for (let k = 0; k < n; k++) {
    const dx = xs[k] - mx, dy = ys[k] - my;
    cov += dx * dy; vx += dx * dx; vy += dy * dy;
  }
  if (vx === 0 || vy === 0) return null;
  return +(cov / Math.sqrt(vx * vy)).toFixed(4);
}

// 结构洞察：规则引擎消费 alerts（level 分区叙事）
function renderInsights(d) {
  const box = document.getElementById('corr-insights');
  if (!box) return;
  box.innerHTML = '';
  const cards = [];
  for (const a of d.alerts) {
    const rule = ALERT_RULES[a.key];
    if (!rule) continue;
    const zone = rule.zones.find(z => a.latest >= z.min);
    if (!zone) continue;
    const arrow = a.latest > a.prev ? '▲' : a.latest < a.prev ? '▼' : '—';
    const dirCls = a.latest > a.prev ? 'up' : a.latest < a.prev ? 'down' : 'neutral';
    cards.push(`
      <div class="corr-insight-card">
        <div class="corr-insight-head"><b>${rule.label}</b><span class="corr-insight-val">${a.latest >= 0 ? '+' : ''}${a.latest.toFixed(2)} <i class="${dirCls}">${arrow}</i></span></div>
        <div class="corr-insight-title">${zone.title}</div>
        <div class="corr-insight-text">${zone.text}</div>
      </div>`);
  }
  box.innerHTML = cards.join('') || '<div class="corr-insight-text">暂无报警数据（等下个交易日 cross_asset 运行）</div>';

  // 报警对迷你趋势（每张洞察卡下方 spark）
  box.querySelectorAll('.corr-insight-card').forEach((el, idx) => {
    const a = d.alerts.filter(x => ALERT_RULES[x.key])[idx];
    if (!a || a.series.length < 3) return;
    const spark = document.createElement('div');
    spark.className = 'corr-spark';
    spark.style.height = '42px';
    el.appendChild(spark);
    const dark = document.body.classList.contains('dark');
    const chart = echarts.init(spark, dark ? 'macroDark' : 'macro', { renderer: 'canvas' });
    chart.setOption({
      grid: { left: 0, right: 0, top: 2, bottom: 0 },
      xAxis: { type: 'category', show: false, data: a.series.map(s => s.date) },
      yAxis: { type: 'value', show: false, min: -1, max: 1 },
      series: [{ type: 'line', data: a.series.map(s => s.value), showSymbol: false, lineStyle: { width: 1.2 }, areaStyle: { opacity: 0.12 } }],
    });
    // 面板销毁随 innerHTML 重建，无需单独管理实例
  });
}

// ── 宏观联动模式（重设计：结论层状态卡 + overlay 卡 + 滚动相关卡）────────────────
// 数据双源：FRED 指标走 /api/macro/correlate，资产序列（ALL_INDICATORS.assets）走 /api/assets/prices
const MACRO_ROLL = 90; // 滚动相关窗口（观测点数；月频指标即 90 个月）

function renderMacroSkeleton(wrap) {
  wrap.innerHTML = `
    <div class="corr-status-row" id="corr-macro-status"><div class="loading" style="height:auto;padding:24px">加载中…</div></div>
    <div class="corr-main" style="grid-template-columns:3fr 2fr">
      <div class="corr-matrix-pane">
        <div class="corr-pane-head">
          <span class="corr-pane-title" id="corr-macro-title">—</span>
          <span class="corr-pane-sub" id="corr-macro-sub"></span>
        </div>
        <div class="corr-macro-chips" id="correlation-custom-controls"></div>
        <div id="correlation-chart" class="corr-macro-chart"></div>
        <div class="corr-pane-foot" id="corr-macro-foot">数据源：FRED · 资产序列 yfinance asset_prices · 混频按日期对齐</div>
      </div>
      <div class="corr-side-pane">
        <div class="corr-pane-head"><span class="corr-pane-title">滚动相关</span><span class="corr-pane-sub">窗口自适应（≤90 观测点）</span></div>
        <div id="corr-macro-roll" class="corr-macro-roll"><div class="loading" style="height:auto;padding:24px">加载中…</div></div>
      </div>
    </div>`;
  // 预设按钮组 + 日期范围组都挂工具栏（renderUI 已建容器）
  const presetsBar = document.getElementById('corr-presets');
  presetsBar.innerHTML = presets.map(p =>
    `<button class="correlation-preset-btn" data-id="${p.id}">${p.name}</button>`
  ).join('');
  presetsBar.querySelectorAll('.correlation-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const preset = presets.find(p => p.id === btn.dataset.id);
      if (preset) loadPreset(preset);
    });
  });
  const dateToolbar = document.getElementById('corr-date-toolbar');
  dateToolbar.style.display = 'flex';
  dateToolbar.innerHTML = MACRO_DATE_RANGES.map(r =>
    `<button class="macro-range-btn${r.value === dateRange ? ' active' : ''}" data-range="${r.value}">${r.label}</button>`
  ).join('');
  dateToolbar.querySelectorAll('.macro-range-btn').forEach(btn => {
    btn.addEventListener('click', () => setDateRange(btn.dataset.range));
  });
}

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
  // 资产序列（与 FRED 指标同图 overlay；数据源 /api/assets/prices）
  assets: ['SPX', 'NDX', 'TLT', 'HYG', 'LQD', 'Gold', 'WTI', 'BTC', 'DXY', 'MOVE'],
};
const ASSET_LABELS = { SPX: '标普500', NDX: '纳斯达克100', TLT: '长期国债 ETF', HYG: '高收益债 ETF', LQD: '投资级债 ETF', Gold: '黄金', WTI: 'WTI 原油', BTC: '比特币', DXY: '美元指数', MOVE: 'MOVE 债市波动' };
const indicatorLabel = (name) => ASSET_LABELS[name] || MACRO_LABELS[name] || name;
const isAsset = (name) => !!ASSET_LABELS[name];

function loadPreset(preset) {
  activePreset = { ...preset };
  document.querySelectorAll('.correlation-preset-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.id === preset.id));
  renderCustomControls();
  loadAndRender();
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
    const axis = activePreset.left_axis.includes(ind) ? '左' : '右';
    const label = document.createElement('span');
    label.textContent = `${indicatorLabel(ind)} (${axis}轴)`;
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
  select.appendChild(new Option('选择指标…', ''));
  for (const [cat, metrics] of Object.entries(ALL_INDICATORS)) {
    const optgroup = document.createElement('optgroup');
    optgroup.label = cat === 'fx' ? 'FX' : cat === 'assets' ? '资产' : cat.charAt(0).toUpperCase() + cat.slice(1);
    metrics.forEach(m => {
      if (!existing.has(m)) {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = `${m} — ${indicatorLabel(m)}`;
        optgroup.appendChild(opt);
      }
    });
    if (optgroup.children.length) select.appendChild(optgroup);
  }
  btn.replaceWith(select);
  select.focus();
  select.addEventListener('change', () => {
    if (select.value) addIndicator(select.value);
  });
  select.addEventListener('blur', () => renderCustomControls()); // 未选时还原为 + 按钮
}

async function loadAndRender() {
  if (!activePreset) return;
  try {
    // 双源拆分：FRED 指标走 correlate，资产序列走 prices（预设 asset 字段或 ALL_INDICATORS.assets）
    const fredNames = activePreset.indicators.filter(n => !isAsset(n));
    const assetNames = activePreset.indicators.filter(isAsset);

    const seriesMap = {};
    if (fredNames.length) {
      // 本地 dev：FastAPI 按 indicators 返回；静态版：构建期转全量 correlate.json，本地过滤
      const res = await fetch(`/api/macro/correlate?indicators=${encodeURIComponent(fredNames.join(','))}`);
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const all = await res.json();
      for (const name of fredNames) {
        const info = all.indicators[name];
        if (!info) continue;
        const filtered = applyDateFilter(info.data, dateRange);
        seriesMap[name] = {
          data: filtered.filter(d => d.value != null).map(d => [d.date, d.value]),
          label: info.label,
        };
      }
    }
    if (assetNames.length) {
      if (!pricesCache) {
        const res = await fetch('/api/assets/prices');
        if (!res.ok) throw new Error(`API error: ${res.status}`);
        pricesCache = await res.json();
      }
      for (const name of assetNames) {
        const rows = pricesCache.prices
          .filter(r => r[name] != null)
          .map(r => ({ date: r.date, value: r[name] }));
        seriesMap[name] = { data: applyDateFilter(rows, dateRange).map(d => [d.date, d.value]), label: indicatorLabel(name) };
      }
    }
    renderMacroStatus(seriesMap);
    renderChart(seriesMap);
    renderRollingCorr(seriesMap);
    seriesMapCache = seriesMap;
  } catch (e) {
    const chartEl = document.getElementById('correlation-chart');
    if (chartEl) chartEl.innerHTML = `<div class="loading">加载失败: ${e.message}</div>`;
  }
}

// 结论层状态卡：每对（左轴 × 右轴）当前区间滚动相关 + 叙事
function corrNarrative(c) {
  if (c == null) return '数据不足';
  if (c >= 0.7) return '高度同向';
  if (c >= 0.3) return '同向联动';
  if (c > -0.3) return '弱相关 · 独立运行';
  if (c > -0.7) return '反向联动';
  return '高度反向';
}

function corrClass(c) {
  if (c == null) return 'neutral';
  if (c >= 0.7) return 'down';   // 高度同向 = 分散化失效，结构恶化
  if (c >= 0.3) return 'neutral';
  return 'up';                   // 负相关 = 有对冲价值
}

function rollingSeries(a, b) {
  // 混频对齐（月频 CPI × 日频 SPX）：按 asof 语义前向填充——每个日期取各自「最新可用值」，
  // 否则月频与日频的日期几乎不相交，精确 join 下有效对永远不足
  const fa = ffillMap(a), fb = ffillMap(b);
  const dates = [...new Set([...fa.dates, ...fb.dates])].sort();
  // 窗口自适应：5Y 月频只有 60 点，固定 90 点窗口会永远凑不够 —— 取总点的 1/3，下限 12
  const win = Math.max(12, Math.min(MACRO_ROLL, Math.floor(dates.length / 3)));
  const out = [];
  for (let k = win - 1; k < dates.length; k++) {
    const xs = [], ys = [];
    for (let m = k - win + 1; m <= k; m++) {
      const va = asof(fa, dates[m]), vb = asof(fb, dates[m]);
      if (va != null && vb != null) { xs.push(va); ys.push(vb); }
    }
    if (xs.length < Math.max(6, Math.floor(win / 2))) { out.push([dates[k], null]); continue; }
    out.push([dates[k], pearson(xs, ys)]); // 水平相关：直接对原始值（非收益率）——宏观指标看同向性
  }
  return out;
}

// [date,value] 对 → {dates: 有观测日期有序数组, values: 对应非空值}（供 asof 前向查询）
function ffillMap(pairs) {
  const clean = pairs.filter(p => p[1] != null).sort((x, y) => x[0] < y[0] ? -1 : 1);
  return { dates: clean.map(p => p[0]), values: clean.map(p => p[1]) };
}

// asof 查询：日期 d 之前（含）最近一次观测值；早于首个观测返回 undefined
function asof(series, d) {
  // 二分找最后一个 <= d 的观测
  let lo = 0, hi = series.dates.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (series.dates[mid] <= d) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans < 0 ? undefined : series.values[ans];
}

function renderMacroStatus(seriesMap) {
  const row = document.getElementById('corr-macro-status');
  if (!row) return;
  const names = activePreset.indicators.filter(n => seriesMap[n]);
  const left = names.filter(n => activePreset.left_axis.includes(n) || (activePreset.left_axis.length === 0 && n === names[0]));
  const right = names.filter(n => !left.includes(n));
  const cards = [];
  for (const l of left) {
    for (const r of right) {
      const series = rollingSeries(seriesMap[l].data, seriesMap[r].data);
      const valid = series.filter(s => s[1] != null);
      const cur = valid.length ? valid[valid.length - 1][1] : null;
      const prev = valid.length > 1 ? valid[valid.length - 2][1] : null;
      const arrow = cur != null && prev != null ? (cur > prev ? '▲' : cur < prev ? '▼' : '—') : '';
      cards.push(`
        <div class="dash-stat corr-status-card" style="cursor:default">
          <div class="dash-stat-label">${indicatorLabel(l)} × ${indicatorLabel(r)}</div>
          <div class="dash-stat-value ${corrClass(cur)}">${cur == null ? '—' : (cur >= 0 ? '+' : '') + cur.toFixed(2)}</div>
          <div class="dash-stat-desc">滚动相关（水平值）· ▲▼ = 较上一窗口回升/回落</div>
          <div class="dash-stat-change">${corrNarrative(cur)} <i class="${cur != null && prev != null && cur !== prev ? (cur > prev ? 'up' : 'down') : 'neutral'}" style="font-style:normal">${arrow}</i></div>
        </div>`);
    }
  }
  row.innerHTML = cards.join('') || '<div class="loading" style="height:auto;padding:16px">至少需要两个序列</div>';
}

function renderRollingCorr(seriesMap) {
  const box = document.getElementById('corr-macro-roll');
  if (!box) return;
  const names = activePreset.indicators.filter(n => seriesMap[n]);
  const left = names.filter(n => activePreset.left_axis.includes(n));
  const right = names.filter(n => !left.includes(n));
  box.innerHTML = '';
  for (const l of left) {
    for (const r of right) {
      const series = rollingSeries(seriesMap[l].data, seriesMap[r].data);
      const card = document.createElement('div');
      card.className = 'corr-insight-card';
      card.innerHTML = `<div class="corr-insight-head"><b>${indicatorLabel(l)} × ${indicatorLabel(r)}</b></div><div class="corr-roll-chart" style="height:120px"></div>
        <div class="corr-insight-text">${corrNarrative(series.filter(s => s[1] != null).slice(-1)[0]?.[1])} · 相关性变化比绝对水平更重要：快速上升 = 两个市场被同一力量驱动。</div>`;
      box.appendChild(card);
      const dom = card.querySelector('.corr-roll-chart');
      const dark = document.body.classList.contains('dark');
      const chart = echarts.init(dom, dark ? 'macroDark' : 'macro', { renderer: 'canvas' });
      chart.setOption({
        grid: { left: 28, right: 8, top: 6, bottom: 18 },
        xAxis: { type: 'time', axisLabel: { fontSize: 9 } },
        yAxis: { type: 'value', min: -1, max: 1, axisLabel: { fontSize: 9 }, splitLine: { show: true, lineStyle: { opacity: .4 } } },
        visualMap: { show: false, min: -1, max: 1, inRange: { color: dark ? ['#60a5fa', '#f87171'] : ['#1d4ed8', '#dc2626'] } },
        series: [{ type: 'line', data: series, showSymbol: false, connectNulls: true, lineStyle: { width: 1.4 } }],
        tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : v.toFixed(2) },
      });
    }
  }
  if (!box.children.length) box.innerHTML = '<div class="corr-insight-text">至少需要两个序列才能计算滚动相关</div>';
}

function renderChart(seriesMap) {
  if (corrChart) { corrChart.dispose(); corrChart = null; }
  if (corrObserver) { corrObserver.disconnect(); corrObserver = null; }

  const chartEl = document.getElementById('correlation-chart');
  chartEl.innerHTML = '';
  if (!activePreset) return;

  // 右轴多序列时量级混叠（如 HYG ~80 与 NDX ~30000 共轴）：右轴序列归一化（期初=100）
  const needDual = activePreset.left_axis.length > 0 && activePreset.right_axis.length > 0;
  const rightNames = activePreset.indicators.filter(n => activePreset.right_axis.includes(n));
  const normRight = needDual && rightNames.length > 1;

  // 卡头：预设名 + 数据截至 + 预设描述（叙事）
  const sub = document.getElementById('corr-macro-sub');
  if (sub) {
    const lastDates = Object.values(seriesMap).map(s => s.data.length ? s.data[s.data.length - 1][0] : null).filter(Boolean);
    sub.textContent = lastDates.length ? `数据截至 ${lastDates.sort().slice(-1)[0]}` : '';
  }
  const desc = document.getElementById('corr-macro-foot');
  if (desc) desc.textContent = (activePreset.description || '数据源：FRED · 资产序列 yfinance asset_prices · 混频按日期对齐') + (normRight ? ' · 右轴多序列已归一化（期初=100）' : '');

  // 序列整理（undefined 归 null —— echarts sampling processor 对 undefined 值崩溃）
  const items = activePreset.indicators
    .map(name => {
      let info = seriesMap[name];
      if (!info || !info.data.length) return null;
      if (normRight && rightNames.includes(name)) {
        const base = info.data.find(d => d[1] != null)?.[1];
        if (base) info = { ...info, data: info.data.map(d => [d[0], d[1] == null ? null : +(d[1] / base * 100).toFixed(2)]) };
      }
      return { name, info, color: MACRO_COLORS[activePreset.indicators.indexOf(name) % MACRO_COLORS.length], isLeft: activePreset.left_axis.includes(name) };
    })
    .filter(Boolean);
  const series = items.map(x => ({
    name: x.info.label || x.name,
    type: 'line',
    data: x.info.data,
    lineStyle: { color: x.color, width: 2 },
    itemStyle: { color: x.color },
    showSymbol: false,
    yAxisIndex: (needDual && !x.isLeft) ? 1 : 0,
    emphasis: { focus: 'series' },
  }));

  // ── 三带（联动全景）：上=overlay｜中=滚动相关｜下=5年分位；dataZoom/十字线三带联动 ──
  // 中/下带只画第一对（自由添加多序列时避免轴爆炸）；单序列时退化为普通 overlay
  const left0 = items.find(x => x.isLeft) || items[0];
  const right0 = items.find(x => !x.isLeft && x !== left0);
  const corr = left0 && right0 ? rollingSeries(seriesMap[left0.name].data, seriesMap[right0.name].data) : [];
  const corrValid = corr.filter(x => x[1] != null);
  const curCorr = corrValid.length ? corrValid[corrValid.length - 1][1] : null;
  const title2 = document.getElementById('corr-macro-title');
  if (title2) title2.textContent = curCorr != null
    ? `${activePreset.name} · ${indicatorLabel(left0.name)}×${indicatorLabel(right0.name)} 滚动相关 ${curCorr >= 0 ? '+' : ''}${curCorr.toFixed(2)}`
    : activePreset.name;

  if (corrValid.length) {
    series.push({
      name: '滚动相关', type: 'line', xAxisIndex: 1, yAxisIndex: 2,
      data: corr, showSymbol: false, connectNulls: true,
      lineStyle: { width: 1.4, color: '#9c27b0' }, itemStyle: { color: '#9c27b0' },
      areaStyle: { opacity: .12, color: '#9c27b0' },
      markLine: { silent: true, symbol: 'none', lineStyle: { opacity: .4, type: 'dashed' }, label: { show: false }, data: [{ yAxis: 0 }] },
    });
    for (const x of [left0, right0].filter(Boolean)) {
      series.push({
        name: `${x.info.label || x.name} 分位`, type: 'line', xAxisIndex: 2, yAxisIndex: 3,
        data: pctRank(x.info.data), showSymbol: false,
        lineStyle: { width: 1.2, color: x.color }, itemStyle: { color: x.color },
      });
    }
  }

  const hasBands = corrValid.length > 0;
  const grid = hasBands
    ? [{ left: 56, right: needDual ? 56 : 24, top: 34, height: '44%' }, { left: 56, right: needDual ? 56 : 24, top: '60%', height: '17%' }, { left: 56, right: needDual ? 56 : 24, top: '82%', height: '13%' }]
    : { left: '3%', right: needDual ? '6%' : '4%', bottom: 56, top: 32, containLabel: true };
  const xAxis = hasBands
    ? [{ type: 'time', gridIndex: 0, axisLabel: { show: false } }, { type: 'time', gridIndex: 1, axisLabel: { show: false } }, { type: 'time', gridIndex: 2 }]
    : [{ type: 'time', boundaryGap: false }];
  const yAxis = hasBands
    ? [
        { type: 'value', gridIndex: 0, scale: true, splitNumber: 4 },
        { type: 'value', gridIndex: 0, scale: true, position: 'right', splitNumber: 4 },
        { type: 'value', gridIndex: 1, min: -1, max: 1, name: '相关', nameTextStyle: { fontSize: 10 } },
        { type: 'value', gridIndex: 2, min: 0, max: 100, interval: 50, axisLabel: { fontSize: 9 } },
      ]
    : [{ type: 'value', scale: true, splitNumber: 4 }];
  if (!hasBands && needDual) yAxis.push({ type: 'value', scale: true, splitNumber: 4, position: 'right' });
  const dataZoom = hasBands
    ? [{ type: 'inside', xAxisIndex: [0, 1, 2] }, { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 4, height: 16 }]
    : [{ type: 'inside' }, { type: 'slider', bottom: 8, height: 20 }];

  const chart = echarts.init(chartEl, document.body.classList.contains('dark') ? 'macroDark' : 'macro', { renderer: 'canvas' });
  corrChart = chart;
  chart.setOption({
    legend: { show: series.length > 1, top: 4, left: 8, type: 'scroll', itemWidth: 14, textStyle: { fontSize: 11 } },
    grid,
    xAxis,
    yAxis,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross', link: hasBands ? [{ xAxisIndex: 'all' }] : [] } },
    dataZoom,
    series,
  });
  corrObserver = new ResizeObserver(() => chart.resize());
  corrObserver.observe(chartEl);
}

// [date,value] 对的 5 年（1260 观测点）滚动百分位
function pctRank(pairs, lookback = 1260) {
  const out = [];
  for (let k = 0; k < pairs.length; k++) {
    const hist = pairs.slice(Math.max(0, k - lookback + 1), k + 1).map(p => p[1]).filter(v => v != null);
    if (hist.length < 30) { out.push([pairs[k][0], null]); continue; }
    out.push([pairs[k][0], +(hist.filter(h => h <= pairs[k][1]).length / hist.length * 100).toFixed(1)]);
  }
  return out;
}

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

function disposeMatrixCharts() {
  if (matrixChart) { matrixChart.dispose(); matrixChart = null; }
  if (drillChart) { drillChart.dispose(); drillChart = null; }
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '关联';
  if (mode === 'asset') {
    document.getElementById('status-count').textContent = crossData
      ? `${crossData.assets.length} 资产 · ${crossData.window}d 相关` : '';
    document.getElementById('status-range').textContent = crossData ? `数据截至 ${crossData.as_of}` : '';
  } else {
    document.getElementById('status-count').textContent = activePreset
      ? `${activePreset.indicators.length} 序列 · ${activePreset.name}` : '';
    const lastDates = activePreset && Object.values(seriesMapCache || {}).map(s => s.data.length ? s.data[s.data.length - 1][0] : null).filter(Boolean);
    document.getElementById('status-range').textContent = lastDates && lastDates.length
      ? `数据截至 ${lastDates.sort().slice(-1)[0]}` : '';
  }
}
