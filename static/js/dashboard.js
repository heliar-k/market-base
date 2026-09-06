// dashboard.js — 市场仪表盘：数据驾驶舱（今日研判结论层 + 跨资产全景 + 六板块快照 + 波动率信号 + 自选清单）
//
// 数据源（4 个端点，后端零新增）：
//   /api/daily-brief          → 报警 + 主导情景 + FOMC + 跨资产变化表（11 行）
//   /api/assets/overview      → 六板块标的快照（最新价 + 日涨跌）
//   /api/volatility/dashboard → 波动率 hero 4 卡 + 信号卡
//   /api/symbols + /api/kline/{sym}?days=5 → 自选清单（localStorage 持久化）

import { reThemeECharts } from './echarts-theme.js';

// 跨资产表行 → 专题页跳转（与今日研判页同款映射）
const LINKS = {
  SPX: '/assets/equities.html', DXY: '/assets/fx.html', BTC: '/assets/crypto.html',
  WTI: '/assets/commodities.html', Gold: '/assets/commodities.html',
  Y10: '/rates/yield-curve.html', VIX: '/volatility/vix.html', HY_OAS: '/credit/',
  RRP: '/liquidity/rrp-tga.html', TGA: '/liquidity/rrp-tga.html',
  NET_LIQ: '/liquidity/fed-balance-sheet.html',
};

// 风险指标：上行 = 压力 = 红（与今日研判页同一着色语义）
const RISK_KEYS = new Set(['VIX', 'HY_OAS']);

// 自选清单（localStorage，默认参考 timsun：10Y/信用/VIX 类核心指标）
const WATCH_KEY = 'guanlan-watchlist';
const DEFAULT_WATCH = ['SPX', 'NVDA'];

let data = {};
let miniCharts = [];

// ── public API ──────────────────────────────────────────────
export async function initDashboard() {
  const root = document.querySelector('.dashboard-view');
  root.classList.remove('placeholder');
  root.innerHTML = '';
  data = {};
  miniCharts = [];
  window.addEventListener('theme-changed', onDashThemeChanged);

  const head = el('div', 'dash-head');
  head.innerHTML = '<span class="dash-head-title">市场仪表盘</span><span class="dash-asof" id="dash-asof"></span>';
  root.appendChild(head);
  root.appendChild(el('div', 'dash-alerts'));
  root.appendChild(el('div', 'dash-scen'));
  const grid = el('div', 'dash-grid dash-grid-1');
  const tableCard = el('div', 'dash-card');
  tableCard.innerHTML = `
    <div class="dash-card-title">跨资产变化 <small class="dash-title-note">涨跌颜色只表示数值方向；Δ5/Δ20 按各序列有效观测计算，点击行进入专题</small></div>
    <div class="dash-table-wrap"><div class="loading">加载中…</div></div>`;
  grid.appendChild(tableCard);
  root.appendChild(grid);
  root.appendChild(el('div', 'dash-grid dash-grid-2'));
  root.appendChild(el('div', 'dash-grid dash-grid-3'));
  root.appendChild(el('div', 'dash-vol'));
  root.appendChild(el('div', 'dash-watch-card dash-card'));

  await refresh();
}

export function cleanup() {
  miniCharts.forEach(c => { try { c.dispose(); } catch (e) { /* ignore */ } });
  miniCharts = [];
  window.removeEventListener('theme-changed', onDashThemeChanged);
}

function onDashThemeChanged() {
  miniCharts = miniCharts.map(c => {
    const dom = c.getDom();
    if (!dom || !dom.isConnected) return c;
    const opts = c.getOption();
    return reThemeECharts(c, dom, opts);
  });
}

export function refresh() {
  return Promise.all([
    refreshBrief(),
    refreshAssets(),
    refreshVol(),
    refreshWatch(),
  ]);
}

export function updateStatus() {
  document.getElementById('status-symbol').textContent = '市场仪表盘';
  document.getElementById('status-range').textContent = '数据驾驶舱';
  document.getElementById('status-count').textContent = '';
}

// ── data loaders ────────────────────────────────────────────
async function refreshBrief() {
  const brief = await settled('/api/daily-brief');
  data.brief = brief;
  renderAsOf();
  renderAlerts();
  renderScenario();
  renderTable();
}

async function refreshAssets() {
  const res = await settled('/api/assets/overview');
  data.assets = res;
  renderAssetSnapshots();
}

async function refreshVol() {
  const res = await settled('/api/volatility/dashboard');
  data.vol = res;
  renderVol();
}

// ── renderers ───────────────────────────────────────────────

// 页头「数据截至」分段（AGENTS 规范：数据截至 源 date · 源 date）
function renderAsOf() {
  const elx = document.getElementById('dash-asof');
  if (!elx) return;
  const d = data.brief;
  if (d.status !== 'fulfilled') { elx.textContent = '数据截至 —（/api/daily-brief 不可用）'; return; }
  const g = d.value.indicators?.groups || {};
  const segs = Object.entries(g).map(([k, v]) => `${k} ${v}`);
  elx.textContent = '数据截至 ' + (segs.length ? segs.join(' · ') : d.value.indicators?.as_of ?? '—');
}

// 报警条 + 主导情景 + FOMC（结论层）
function renderAlerts() {
  const elx = root().querySelector('.dash-alerts');
  const d = data.brief;
  if (!elx) return;
  if (d.status !== 'fulfilled') { elx.innerHTML = ''; return; }
  const v = d.value;
  const alerts = v.alerts || [];
  const matched = (v.scenarios || []).filter(s => s.matched);
  const headline = matched.length
    ? `主导情景：${matched.map(s => s.title).join(' + ')}`
    : '跨资产信号分化，无主导情景';

  let fomcHTML = '';
  const fomc = v.fomc;
  if (fomc?.next) {
    const days = Math.round((new Date(fomc.next.year, fomc.next.month - 1, fomc.next.end_day) - new Date()) / 86400000);
    const next = `${fomc.next.year}-${String(fomc.next.month).padStart(2, '0')}-${String(fomc.next.end_day).padStart(2, '0')}`;
    fomcHTML = `<span class="dash-chip">FOMC ${next}${days > 0 ? `（${days} 天后）` : '（进行中）'}</span>`;
  }

  elx.innerHTML = `
    <div class="dash-conclusion">
      <span class="dash-badge ${matched.length ? 'bull' : 'flat'}">${headline}</span>
      ${alerts.map(a => `<span class="dash-badge alert" title="${esc(a.text)}">⚠ ${esc(a.title)}</span>`).join('')}
      ${fomcHTML}
    </div>`;
}

// 情景卡（一句话 + 命中条件）——精简版，详情在今日研判页
function renderScenario() {
  const elx = root().querySelector('.dash-scen');
  const d = data.brief;
  if (!elx) return;
  if (d.status !== 'fulfilled') { elx.innerHTML = ''; return; }
  const scens = (d.value.scenarios || []).filter(s => s.matched);
  if (!scens.length) { elx.innerHTML = ''; return; }
  elx.innerHTML = scens.map(s => `
    <div class="dash-card dash-scen-card" title="${esc(s.desc)}">
      <div class="dash-scen-title">${esc(s.title)} <span class="dash-chip on">规则命中</span></div>
      <div class="dash-scen-desc">${esc(s.desc)}</div>
      <div class="dash-scen-evidence">${(s.evidence || []).map(e => `<span>${esc(e)}</span>`).join('')}</div>
      <div class="dash-scen-refute">反证：${esc(s.refute)}</div>
    </div>`).join('');
}

// 跨资产变化表（替换原四卡 + 迷你图 + 股指一览）
function renderTable() {
  const wrap = root().querySelector('.dash-table-wrap');
  const d = data.brief;
  if (!wrap) return;
  if (d.status !== 'fulfilled') {
    wrap.innerHTML = '<div class="loading">今日研判数据加载失败，请确认服务已启动（uv run python -m src.server）</div>';
    return;
  }
  const rows = d.value.indicators?.rows || [];
  const maxAsOf = d.value.indicators?.as_of;
  wrap.innerHTML = `
    <div class="dash-table-wrap"><table class="dash-table">
      <thead><tr><th>指标 / 研究入口</th><th>最新值</th><th>Δ5 观测</th><th>Δ20 观测</th><th>近 20 观测</th><th>数据截至</th><th>时效</th></tr></thead>
      <tbody>${rows.map(r => {
        const [fresh, cls] = freshness(r.as_of, maxAsOf);
        const link = LINKS[r.key];
        const nameCell = link ? `<a href="${link}" target="_blank">${r.name} ↗</a>` : r.name;
        const spark = sparkSVG(r.spark);
        return `<tr class="dash-row-click" title="${esc(r.note || '')}">
          <td>${nameCell}</td>
          <td style="font-weight:600">${fmtLast(r.unit, r.last)}</td>
          <td>${fmtChg(r.unit, r.chg?.d5)}</td>
          <td>${fmtChg(r.unit, r.chg?.d20)}</td>
          <td>${spark}</td>
          <td style="color:var(--text-muted)">${r.as_of ?? '—'}</td>
          <td class="${cls}" style="font-size:.85em">${fresh}</td>
        </tr>`;
      }).join('')}</tbody>
    </table></div>`;
}

// 六板块快照（assets/overview tables）
function renderAssetSnapshots() {
  const grid = root().querySelector('.dash-grid-2');
  const res = data.assets;
  if (!grid) return;
  if (res.status !== 'fulfilled' || !res.value?.tables) {
    grid.innerHTML = '<div class="loading">资产快照加载失败</div>';
    return;
  }
  const boards = [
    ['equities', '股指'], ['bonds', '债券'], ['commodities', '商品'],
    ['etfs', 'ETF'], ['crypto', '加密'], ['fx', '外汇'],
  ];
  grid.innerHTML = boards.map(([key, label]) => {
    const rows = res.value.tables[key] || [];
    if (!rows.length) return '';
    return `<div class="dash-card"><div class="dash-card-title">${label} <small class="dash-title-note">${rows[0]?.date ?? ''}</small></div>
      <div class="dash-snap">${rows.map(r => {
        const cls = r.chg_pct == null || r.chg_pct === 0 ? 'neutral' : (r.chg_pct > 0 ? 'up' : 'down');
        const sign = r.chg_pct > 0 ? '+' : '';
        return `<div class="dash-snap-row">
          <span class="dash-snap-name">${esc(r.name)}</span>
          <span class="dash-snap-val">${fmtNum(r.last)}</span>
          <span class="${cls}">${r.chg_pct == null ? '—' : sign + r.chg_pct.toFixed(2) + '%'}</span>
        </div>`;
      }).join('')}</div></div>`;
  }).join('');
}

// 波动率：hero 4 卡 + 信号卡
function renderVol() {
  const elx = root().querySelector('.dash-vol');
  const res = data.vol;
  if (!elx) return;
  if (res.status !== 'fulfilled' || !res.value?.hero) {
    elx.innerHTML = '';
    return;
  }
  const v = res.value;
  const hero = (v.hero.cards || []).map(c => `
    <div class="dash-stat" style="cursor:default">
      <div class="dash-stat-value">${fmtNum(c.value)}${c.symbol === 'SKEW' ? '' : ''}</div>
      <div class="dash-stat-label">${esc(c.symbol)}</div>
      <div class="dash-stat-desc">${esc(c.name)}</div>
      <div class="dash-stat-change ${c.chg1d > 0 ? 'up' : c.chg1d < 0 ? 'down' : 'neutral'}">${c.chg1d != null ? (c.chg1d > 0 ? '+' : '') + c.chg1d.toFixed(2) + '%' : ''} 1D</div>
    </div>`).join('');
  const signals = (v.signals || []).map(s => `
    <div class="dash-card dash-sig-card">
      <div class="dash-scen-title">${esc(s.title)}</div>
      <div class="dash-scen-desc">${esc(s.metric)}</div>
      <div class="dash-scen-desc">${esc(s.text)}</div>
      ${s.advice ? `<div class="dash-scen-refute">应对：${esc(s.advice)}</div>` : ''}
    </div>`).join('');
  elx.innerHTML = `
    <div class="dash-vol-hero">${hero}</div>
    ${signals ? `<div class="dash-vol-signals">${signals}</div>` : ''}`;
}

// 自选清单（localStorage 持久化；标的来自 /api/symbols + /api/kline）
async function refreshWatch() {
  const card = root().querySelector('.dash-watch-card');
  if (!card) return;
  let list;
  try { list = JSON.parse(localStorage.getItem(WATCH_KEY)) || DEFAULT_WATCH; }
  catch { list = DEFAULT_WATCH; }
  const syms = (await settled('/api/symbols'));
  const all = (syms.status === 'fulfilled' ? syms.value : []).map(s => s.name ?? s);
  const valid = list.filter(s => all.includes(s));
  const results = await Promise.all(
    valid.map(s => settled(`/api/kline/${s}?days=5`))
  );
  card.innerHTML = `
    <div class="dash-card-title">自选清单
      <span id="dash-watch-add-wrap">
        <select id="dash-watch-add" style="margin-left:8px">
          <option value="">＋添加…</option>
          ${all.filter(s => !valid.includes(s)).map(s => `<option>${s}</option>`).join('')}
        </select>
      </span>
      <small class="dash-title-note">点击行进入技术分析</small>
    </div>
    <div class="dash-watchlist">${valid.length ? valid.map((sym, i) => {
      const r = results[i];
      if (r.status !== 'fulfilled' || !Array.isArray(r.value) || r.value.length < 2) {
        return watchRow(sym, '--', null);
      }
      const arr = r.value;
      const price = arr[arr.length - 1].close;
      const prev = arr[arr.length - 2].close;
      const pct = prev ? ((price - prev) / prev * 100) : 0;
      return watchRow(sym, fmtNum(price, 2), pct);
    }).join('') : '<div class="loading">暂无自选，从下拉框添加</div>'}</div>`;

  card.querySelector('#dash-watch-add')?.addEventListener('change', e => {
    if (!e.target.value) return;
    valid.push(e.target.value);
    localStorage.setItem(WATCH_KEY, JSON.stringify(valid));
    refreshWatch();
  });
  // 删除：右键或按住 Alt 点击
  card.querySelectorAll('[data-watch-del]').forEach(elx => {
    elx.addEventListener('click', e => {
      if (!e.altKey) return;
      e.stopPropagation();
      const sym = elx.dataset.watchDel;
      const next = valid.filter(s => s !== sym);
      localStorage.setItem(WATCH_KEY, JSON.stringify(next));
      refreshWatch();
    });
  });
  card.querySelectorAll('[data-go-stock]').forEach(row => {
    row.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('go-stock', { detail: row.dataset.goStock }));
    });
  });
}

function watchRow(sym, price, pct) {
  if (pct == null) {
    return `<div class="dash-watch-row" data-go-stock="${sym}" title="Alt+点击删除">
      <span class="dash-watch-sym">${sym}</span><span class="dash-watch-price">${price}</span><span>--</span></div>`;
  }
  const cls = pct >= 0 ? 'up' : 'down';
  const sign = pct >= 0 ? '+' : '';
  return `<div class="dash-watch-row" data-go-stock="${sym}" data-watch-del="${sym}" title="点击进入技术分析 · Alt+点击删除">
    <span class="dash-watch-sym">${sym}</span>
    <span class="dash-watch-price">${price}</span>
    <span class="${cls}">${sign}${pct.toFixed(2)}%</span>
  </div>`;
}

// ── helpers（与今日研判页同款格式化）────────────────────────
function root() { return document.querySelector('.dashboard-view'); }

function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

async function settled(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    return { status: 'fulfilled', value: await res.json() };
  } catch (e) {
    return { status: 'rejected', reason: String(e) };
  }
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtNum(n, p = 2) {
  if (n == null || isNaN(n)) return '--';
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: p });
}

function fmtLast(unit, v) {
  if (v == null) return '—';
  if (unit === 'bn') return v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'T' : v >= 1e3 ? '$' + Math.round(v / 1e3 * 10) / 10 + 'B' : '$' + Math.round(v).toLocaleString('en-US') + 'M';
  if (unit === 'bp') return Number(v).toFixed(1) + 'bp';
  if (unit === 'pt') return Number(v).toFixed(2);
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function fmtChg(unit, v) {
  if (v == null) return '<span style="color:var(--text-secondary)">—</span>';
  const s = v > 0 ? '+' : '';
  if (unit === 'bp') return `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${s}${v.toFixed(1)}bp</span>`;
  if (unit === 'pt') return `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${s}${v.toFixed(2)}pt</span>`;
  if (unit === 'bn') {
    const a = Math.abs(v);
    const f = a >= 1e6 ? (v / 1e6).toFixed(2) + 'T' : a >= 1e3 ? '$' + Math.round(v / 1e3) + 'B' : '$' + Math.round(v) + 'M';
    return `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${s}${f}</span>`;
  }
  return `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${s}${v.toFixed(2)}%</span>`;
}

// 时效：行数据日期距全表最新 ≤4 自然日 → 正常（覆盖周末），否则滞后
function freshness(rowAsOf, maxAsOf) {
  if (!rowAsOf || !maxAsOf) return ['—', ''];
  const lag = (new Date(maxAsOf) - new Date(rowAsOf)) / 86400000;
  return lag <= 4 ? ['时效正常', ''] : [`滞后 ${Math.round(lag)} 天`, 'down'];
}

function sparkSVG(points) {
  if (!points || points.length < 2) return '';
  const vals = points.map(p => p[1]);
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const W = 110, H = 30, step = W / (points.length - 1);
  const xy = points.map((p, i) => `${(i * step).toFixed(1)},${(H - 2 - (p[1] - min) / span * (H - 4)).toFixed(1)}`);
  const up = vals[vals.length - 1] >= vals[0];
  const color = up ? 'var(--up, #26a69a)' : 'var(--down, #ef5350)';
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="vertical-align:middle">
    <polyline points="${xy.join(' ')}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}
