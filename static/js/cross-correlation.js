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
let dateRange = 'all';

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
    <div class="corr-mode-title" id="corr-mode-title">${mode === 'asset' ? '跨资产相关性 · 结构观察' : '宏观指标联动 overlay'}</div>
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
  if (sub) sub.textContent = `as of ${d.as_of} · ${d.window} 日滚动 · 点格子看滚动相关`;
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

// ── 宏观联动模式（保留原 overlay 功能）─────────────────────────────────────
function renderMacroSkeleton(wrap) {
  wrap.innerHTML = `
    <div class="correlation-presets" id="corr-presets"></div>
    <div class="correlation-controls" id="corr-macro-controls"></div>
    <div id="correlation-chart"></div>`;
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
  const controls = document.getElementById('corr-macro-controls');
  const dateToolbar = document.getElementById('corr-date-toolbar');
  dateToolbar.style.display = 'flex';
  dateToolbar.innerHTML = MACRO_DATE_RANGES.filter(r => r.months <= 60).map(r =>
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
};

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

async function loadAndRender() {
  if (!activePreset) return;
  try {
    const indicators = activePreset.indicators.join(',');
    // 本地 dev：FastAPI 按 indicators 返回；静态版：构建期转全量 correlate.json，本地过滤
    const res = await fetch(`/api/macro/correlate?indicators=${encodeURIComponent(indicators)}`);
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

  const chart = echarts.init(chartEl, document.body.classList.contains('dark') ? 'macroDark' : 'macro', { renderer: 'canvas' });
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
    document.getElementById('status-range').textContent = crossData ? `as of ${crossData.as_of}` : '';
  } else {
    document.getElementById('status-count').textContent = activePreset
      ? `${activePreset.indicators.length} 指标` : '';
    document.getElementById('status-range').textContent = activePreset
      ? activePreset.name || '' : '';
  }
}
