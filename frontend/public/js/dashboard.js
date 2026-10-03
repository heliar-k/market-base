// dashboard.js — 市场仪表盘：数据驾驶舱（今日研判结论层 + 波动率 KPI + 六板块快照 + 信号卡 + 自选清单）
//
// 数据源（5 个端点）：
//   /api/home-lines           → 首屏「今日一句话」结论层（9 个专题引擎各一行，issue #21）
//   /api/daily-brief          → 结构报警 + 主导情景徽章（表与情景卡本体在 /daily/，不重复）
//   /api/assets/overview      → 六板块标的快照（最新价 + 日涨跌）
//   /api/volatility/dashboard → 波动率 hero 4 卡 + 信号卡
//   /api/symbols + /api/kline/{sym}?days=5 → 自选清单（localStorage 持久化）

import { SITE_NAV } from './site-nav.js';

// 专题页映射（SITE_NAV 唯一数据源：组键 + 子页键 → page），今日一句话用
const NAV_PAGES = (() => {
  const pages = new Map();
  for (const g of SITE_NAV.groups) {
    pages.set(g.key, g.page);
    (g.items || []).forEach(i => pages.set(i.key, i.page));
  }
  return pages;
})();

// 自选清单（localStorage，默认参考 timsun：10Y/信用/VIX 类核心指标）
const WATCH_KEY = 'guanlan-watchlist';
const DEFAULT_WATCH = ['SPX', 'NVDA'];

let data = {};

// ── public API ──────────────────────────────────────────────
export async function initDashboard() {
  const root = document.querySelector('.dashboard-view');
  root.classList.remove('placeholder');
  root.innerHTML = '';
  data = {};

  const head = el('div', 'dash-head');
  head.innerHTML = '<span class="dash-head-title">市场仪表盘</span><span class="dash-asof" id="dash-asof"></span>';
  root.appendChild(head);
  root.appendChild(el('div', 'dash-lines'));
  root.appendChild(el('div', 'dash-alerts'));
  root.appendChild(el('div', 'dash-brief-link'));
  root.appendChild(el('div', 'dash-kpi'));
  root.appendChild(el('div', 'dash-grid dash-grid-2'));
  root.appendChild(el('div', 'dash-vol'));
  root.appendChild(el('div', 'dash-watch-card dash-card'));

  await refresh();
}

export function cleanup() {
  data = {};
}

export function refresh() {
  return Promise.all([
    refreshLines(),
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
  // 一句话的主次靠 brief 的 topics，brief 后到 → 重绘一次 lines
  renderLines();
  renderBriefLink();
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

// 今日一句话（首屏结论层，issue #21）：文案全来自 Python 规则引擎，前端只渲染不计算
async function refreshLines() {
  data.lines = await settled('/api/home-lines');
  renderLines();
}

function renderLines() {
  const box = root().querySelector('.dash-lines');
  if (!box) return;
  const d = data.lines;
  const lines = d && d.status === 'fulfilled' ? d.value.lines || [] : [];
  if (!lines.length) {
    const why = d && d.status === 'fulfilled' ? '各专题数据缺失' : '接口 /api/home-lines 不可达';
    box.innerHTML = '<div class="dash-card"><div class="dash-line-empty">' + why
      + '，暂无当日研判一句话。先拉数据（如 <code>./bin/fetch_fred</code>），再跑 '
      + '<code>uv run python -m src.export_pages</code> 生成静态 JSON（本地服务同一路径）。'
      + '</div></div>';
    return;
  }
  // 主次不自己判：规则引擎已在 alerts / 命中情景上标了 topics（指标→专题键），
  // 这里只做集合求交（不靠前端扫文案关键词）。
  const hot = hotTopics();
  const on = lines.filter(l => hot.has(l.key));
  const off = lines.filter(l => !hot.has(l.key));
  // 全冷时不分层：否则退化成 9 个没正文的胶囊，这一段就没用了
  const cards = on.length ? on : lines;
  const chips = on.length ? off : [];
  // 标题不重复写时效：页头 dash-asof 已是全局口径，这里再写一个不同日期只会让人怀疑哪个对；
  // 各引擎自己的观测日放在卡 title 里（悬停可查）。
  box.innerHTML = `
    <div class="dash-sec-head">今日一句话 <small class="dash-title-note">按专题聚合 · 点击进入对应研判</small></div>
    <div class="dash-line-cards">${cards.map(l => lineCard(l, hot.has(l.key))).join('')}</div>
    ${chips.length ? `<div class="dash-calm"><span class="dash-calm-k">平稳</span>${chips.map(calmChip).join('')}</div>` : ''}`;
}

// 有信号的专题卡：标题 + 完整一句话（不截断）+ 行末进入提示
function lineCard(l, hot) {
  const inner = `<div class="dash-line-card-t">${esc(l.label)}</div>
    <div class="dash-line-card-x">${esc(l.text)}</div>`;
  const page = NAV_PAGES.get(l.key);
  if (!page) return `<div class="dash-line-card calm">${inner}</div>`;
  return `<a class="dash-line-card${hot ? ' hot' : ' calm'}" href="${page}" target="_blank"
    title="${esc(l.label)}专题 · ${esc(l.as_of || '')}">${inner}<span class="dash-line-card-go">进入研判 →</span></a>`;
}

// 平稳专题只留一颗胶囊：正文今天没有可说的，细节进专题页看
function calmChip(l) {
  return `<a href="${NAV_PAGES.get(l.key)}" target="_blank"
    title="${esc(l.label)}专题 · ${esc(l.as_of || '')}">${esc(l.label)}</a>`;
}


// 正在响的专题集合（报警 + 命中情景的 topics，两者都由后端规则引擎声明）
function hotTopics() {
  const b = data.brief;
  if (!b || b.status !== 'fulfilled') return new Set();
  const v = b.value;
  const src = [...(v.alerts || []), ...(v.scenarios || []).filter(s => s.matched)];
  return new Set(src.flatMap(x => x.topics || []));
}

// 页头「数据截至」分段（AGENTS 规范：数据截至 源 date · 源 date）
function renderAsOf() {
  const elx = document.getElementById('dash-asof');
  if (!elx) return;
  const d = data.brief;
  if (d.status !== 'fulfilled') { elx.textContent = '数据不可用（brief 端点未响应，确认服务已启动）'; return; }
  const g = d.value.indicators?.groups || {};
  // 时效文案只能由 R.asOf 组装（AGENTS 第 8 节）：多段用 [源, date] 数组，空段自动丢
  const segs = Object.entries(g).map(([k, v]) => [k, v]);
  elx.textContent = R.asOf(segs.length ? segs : [d.value.indicators?.as_of]);
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
  const nx = R.nextMeeting(v.fomc);
  if (nx) {
    // 静态 JSON 冻结了构建期日历：按打开页面时间重选下一场（R.nextMeeting 单源）；
    // 天数口径统一 end_day + ceil
    const days = Math.ceil((new Date(nx.year, nx.month - 1, nx.end_day) - new Date()) / 86400000);
    const next = `${nx.year}-${String(nx.month).padStart(2, '0')}-${String(nx.end_day).padStart(2, '0')}`;
    fomcHTML = `<span class="dash-chip">FOMC ${next}${days > 0 ? `（${days} 天后）` : '（进行中）'}</span>`;
  }

  // 预测市场锚点：衰退概率（Polymarket）
  let pmHTML = '';
  const pm = v.polymarket?.recession;
  if (pm?.prob != null) {
    const pct = (pm.prob * 100).toFixed(1).replace(/\.0$/, '');
    const y = (pm.end_date || '').slice(0, 4);
    pmHTML = `<span class="dash-chip" title="${esc(pm.title)}（Polymarket ${esc(v.polymarket.as_of || '')}）">衰退 ${y} ${pct}%</span>`;
  }

  elx.innerHTML = `
    <div class="dash-conclusion">
      <span class="dash-badge ${matched.length ? 'bull' : 'flat'}">${headline}</span>
      ${alerts.map(a => `<span class="dash-badge alert" title="${esc(a.text)}">⚠ ${esc(a.title)}</span>`).join('')}
      ${fomcHTML}
      ${pmHTML}
    </div>`;
}

// 跨资产变化表 / 情景卡本体在 /daily/（同端点同 11 行，仪表盘不复制一遍），这里只留入口
function renderBriefLink() {
  const box = root().querySelector('.dash-brief-link');
  const d = data.brief;
  if (!box) return;
  if (d.status !== 'fulfilled') { box.innerHTML = ''; return; }
  const v = d.value;
  const n = (v.indicators?.rows || []).length;
  const sc = (v.scenarios || []).filter(s => s.matched).length;
  box.innerHTML = `<a class="dash-brief-a" href="${SITE_NAV.home.page}" target="_blank">
    <span class="dash-brief-t">跨资产变化表 · 情景与反证</span>
    <span class="dash-brief-d">${n} 个指标的 Δ5/Δ20 与近 20 观测走势${sc ? `，当前命中 ${sc} 个情景` : ''}</span>
    <span class="dash-brief-go">↗</span></a>`;
}

// 六板块快照（assets/overview tables）
function renderAssetSnapshots() {
  const grid = root().querySelector('.dash-grid-2');
  const res = data.assets;
  if (!grid) return;
  if (res.status !== 'fulfilled' || !res.value?.tables) {
    grid.innerHTML = '<div class="loading">资产快照加载失败 · 运行 ./bin/fetch_yfinance 后刷新</div>';
    return;
  }
  const boards = [
    ['equities', '股指'], ['bonds', '债券'], ['commodities', '商品'],
    ['etfs', 'ETF'], ['crypto', '加密'], ['fx', '外汇'],
  ];
  grid.innerHTML = boards.map(([key, label]) => {
    const rows = res.value.tables[key] || [];
    if (!rows.length) return '';
    return `<div class="dash-card"><div class="dash-card-title">${label} <small class="dash-title-note">日涨跌 · ${rows[0]?.date ?? ''}</small></div>
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

// 波动率：hero 4 卡（KPI 卡行单源 R.cards）+ 信号卡
function renderVol() {
  const kpi = root().querySelector('.dash-kpi');
  const elx = root().querySelector('.dash-vol');
  const res = data.vol;
  if (!kpi || !elx) return;
  if (res.status !== 'fulfilled' || !res.value?.hero) {
    kpi.innerHTML = '';
    elx.innerHTML = '';
    return;
  }
  const v = res.value;
  // 卡行不再自写 .dash-vol-hero + .dash-stat（与全站 KPI 卡双轨）；涨跌 chip 走 R.chgSpan
  kpi.replaceChildren(R.cards((v.hero.cards || []).map(c => ({
    label: c.symbol,
    value: fmtNum(c.value),
    sub: `${esc(c.name)} · ${R.chgSpan(c.chg1d)} 1D`,
  }))));
  const signals = (v.signals || []).map(s => `
    <div class="dash-card dash-sig-card">
      <div class="dash-sig-title">${esc(s.title)}</div>
      <div class="dash-sig-metric">${esc(s.metric)}</div>
      <div class="dash-sig-text">${esc(s.text)}</div>
      ${s.advice ? `<div class="dash-sig-advice">应对：${esc(s.advice)}</div>` : ''}
    </div>`).join('');
  elx.innerHTML = signals ? `<div class="dash-vol-signals">${signals}</div>` : '';
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
    // 纯 div 行只有 click 监听，键盘进不去：补 tabindex + Enter/Space 转发
    row.tabIndex = 0;
    row.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('go-stock', { detail: row.dataset.goStock }));
    });
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); row.click(); }
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

// HTML 转义单源走 rates-common.js 的 R.esc（本文件历史本地版已删）
const esc = R.esc;

function fmtNum(n, p = 2) {
  if (n == null || isNaN(n)) return '--';
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: p });
}
