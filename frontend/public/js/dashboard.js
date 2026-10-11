// dashboard.js — 市场仪表盘：B 方案「每日头版」版式（桌面/手机双端）
//
// 信息组织逻辑（编辑排版）：头条是全页唯一视觉锚点（共识 verdict 大字），
// 其下按「叙事（今日一句话）×数据（波动率）」双栏，再行情大表，再自选。
// DOM 顺序 = 手机阅读优先级（头条 → 波动率 → 快照 → 一句话 → 自选，数字先于叙事）；
// 桌面 ≥1024 由 .dash-flow 的 grid-template-areas 重排成头版，同一份 DOM 两端各自布局。
//
// 数据源（5 个端点）：
//   /api/consensus            → 头条 verdict（市场/宏观状态 + 成员投票）
//   /api/daily-brief          → 主导情景/报警 + FOMC/衰退背景 + 数据截至
//   /api/home-lines           → 今日一句话（9 个专题引擎各一行，issue #21）
//   /api/assets/overview      → 市场快照单表（六板块全量行，板块名做行内节头）
//   /api/volatility/dashboard → 波动率 hero 4 卡 + 信号列表
//   /api/symbols + /api/kline/{sym}?days=5 → 自选 chips（localStorage 持久化）

import { SITE_NAV } from './site-nav.js';

// 专题页映射（SITE_NAV 唯一数据源：组键 + 子页键 → page），头条/各节入口用
const NAV_PAGES = (() => {
  const pages = new Map();
  for (const g of SITE_NAV.groups) {
    pages.set(g.key, g.page);
    (g.items || []).forEach(i => pages.set(i.key, i.page));
  }
  return pages;
})();

// 自选清单（localStorage，默认参考 timsun：核心指数/个股）
const WATCH_KEY = 'guanlan-watchlist';
const DEFAULT_WATCH = ['SPX', 'NVDA'];

let data = {};

// ── public API ──────────────────────────────────────────────
export async function initDashboard() {
  const root = document.querySelector('.dashboard-view');
  root.classList.remove('placeholder');
  root.innerHTML = '';
  data = {};

  // 页头只留刊名；时效与状态全部下沉到头条区（B 方案：唯一锚点）
  const head = el('div', 'dash-head');
  head.innerHTML = '<span class="dash-head-title">市场仪表盘</span>';
  root.appendChild(head);

  // 手机端 sticky 细条：滚过头条后滑入（risk-on · ⚠n · VIX），桌面隐藏
  const sticky = buildSticky();
  root.appendChild(sticky);

  // 头版 flow：DOM 顺序 = 手机阅读优先级；桌面 ≥1024 把块装进两个列容器
  // （左 60% = 叙事+快照，右 40% = 波动率+自选），两端高度各自堆叠不留洞；
  // <1024 时列容器 display:contents 溶解，用 order 恢复手机优先级
  const flow = el('div', 'dash-flow');
  flow.appendChild(heroShell());
  const colA = el('div', 'dash-col dash-col-a');
  colA.append(linesShell(), snapShell());
  const colB = el('div', 'dash-col dash-col-b');
  colB.append(volShell(), watchShell());
  flow.append(colA, colB);
  root.appendChild(flow);

  setupStickyObserver(sticky);

  await refresh();
}

export function cleanup() {
  data = {};
}

export function refresh() {
  return Promise.all([
    refreshConsensus(),
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

// ── section shells（static skeletons；渲染器按 id 填充）────────
// 头条区：verdict（共识）+ 情景/报警（brief）+ 背景行（FOMC/衰退/入口/数据截至）
function heroShell() {
  const s = el('section', 'dash-hero');
  s.innerHTML = `
    <div class="dash-hero-verdict" id="dash-hero-verdict"><span class="loading">共识加载中…</span></div>
    <div class="dash-hero-sub" id="dash-hero-sub"></div>
    <div class="dash-hero-meta"><span id="dash-hero-bg"></span><span id="dash-asof"></span></div>`;
  return s;
}

// 波动率：节头 + hero 4 卡（R.cards 单源，列内 2×2）+ 信号列表
function volShell() {
  const s = el('section', 'dash-vol-sec');
  const page = NAV_PAGES.get('vol');
  s.innerHTML = `
    <div class="dash-sec-head">波动率${page ? ` <a class="dash-sec-link" href="${page}" target="_blank">进入专题 →</a>` : ''} <small class="dash-title-note">1 日变化</small></div>
    <div class="dash-kpi"></div>
    <div class="dash-vol"></div>`;
  return s;
}

// 市场快照：单表（板块名做行内节头，桌面分栏 + 栏间线）；每板块展示前 4 行，
// 页脚行统一（截断 → 全部 N 项 / 未截断 → 进入专题），控制左栏高度与右栏平衡
function snapShell() {
  const s = el('section', 'dash-snap-sec');
  const page = NAV_PAGES.get('assets');
  s.innerHTML = `
    <div class="dash-sec-head">市场快照${page ? ` <a class="dash-sec-link" href="${page}" target="_blank">进入大类资产 →</a>` : ''}</div>
    <div class="dash-snap-table dash-card"></div>`;
  return s;
}

// 今日一句话：叙事列（hot 卡全文 + 平稳弱化索引）
function linesShell() {
  const s = el('section', 'dash-lines-sec');
  s.innerHTML = `
    <div class="dash-sec-head">今日一句话 <small class="dash-title-note">按专题聚合 · 点击进入对应研判</small></div>
    <div class="dash-lines"></div>`;
  return s;
}

// 自选清单：chips 一行（点击进技术分析，× 删除）
function watchShell() {
  const s = el('section', 'dash-watch-sec');
  s.innerHTML = `
    <div class="dash-sec-head">自选清单 <small class="dash-title-note">点击进入技术分析 · × 删除</small>
      <span class="dash-watch-add-wrap"><select id="dash-watch-add"><option value="">＋添加…</option></select></span>
    </div>
    <div class="dash-watch-chips" id="dash-watch-chips"></div>`;
  return s;
}

// ── data loaders ────────────────────────────────────────────
async function refreshConsensus() {
  data.consensus = await settled('/api/consensus');
  renderConsensus();
}

async function refreshBrief() {
  data.brief = await settled('/api/daily-brief');
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

// 头条 verdict：市场/宏观状态大字 + 成员投票 chips（ADR-0004 /api/consensus）
function renderConsensus() {
  const box = document.getElementById('dash-hero-verdict');
  const stv = document.getElementById('dash-sticky-verdict');
  if (!box) return;
  const d = data.consensus;
  const c = d && d.status === 'fulfilled' ? d.value : null;
  if (!c || !c.market || !c.macro) {
    box.innerHTML = '<span class="dash-hero-na">共识数据缺失（/api/consensus 不可达，确认服务已启动或重跑 export_pages）</span>';
    if (stv) stv.textContent = '';
    return;
  }
  // 状态→色档由 API 下发 cls（on/off/div/na），复用 cons-state 色档，字号在头条放大
  const state = (label, card) => {
    const cls = card.cls || 'na';
    return `<span class="dash-hero-item"><small>${label}</small><b class="cons-state cons-state--${cls}">${esc(card.state)}</b></span>`;
  };
  const chip = (m) => {
    const cls = !m.present ? 'cons-vote--absent' : (m.vote === 'on' ? 'cons-vote--on' : m.vote === 'off' ? 'cons-vote--off' : 'cons-vote--abst');
    const icon = !m.present ? '×' : (m.vote === 'on' ? '✔' : m.vote === 'off' ? '✘' : '—');
    const href = (c.page_links || {})[m.name] || '#';
    return `<a class="cons-vote ${cls}" href="${href}">${m.name}<i>${icon}</i></a>`;
  };
  box.innerHTML = `
    <div class="dash-hero-verdict-line">
      ${state('市场', c.market)}<span class="dash-hero-sep" aria-hidden="true">·</span>${state('宏观', c.macro)}
      ${c.finalized === false ? '<span class="cons-intraday">盘中临时值</span>' : ''}
    </div>
    <div class="dash-hero-votes">${c.market.members.map(chip).join('')}<span class="dash-hero-votes-sep" aria-hidden="true"></span>${c.macro.members.map(chip).join('')}</div>`;
  if (stv) stv.innerHTML = `<b class="cons-state cons-state--${c.market.cls || 'na'}">${esc(c.market.state)}</b><span class="dash-sticky-macro">宏观 ${esc(c.macro.state)}</span>`;
}

// 头条次级行：主导情景 + 报警（语义色文字，不再做胶囊）
function renderAlerts() {
  const elx = document.getElementById('dash-hero-sub');
  const sta = document.getElementById('dash-sticky-alert');
  if (!elx) return;
  const d = data.brief;
  if (d.status !== 'fulfilled') { elx.innerHTML = ''; if (sta) sta.textContent = ''; return; }
  const v = d.value;
  const alerts = v.alerts || [];
  const matched = (v.scenarios || []).filter(s => s.matched);
  const headline = matched.length
    ? `主导情景：${matched.map(s => s.title).join(' + ')}`
    : '跨资产信号分化，无主导情景';
  elx.innerHTML = `
    <div class="dash-hero-scen${matched.length ? ' hot' : ''}">${esc(headline)}</div>
    ${alerts.map(a => `<div class="dash-hero-alert" title="${esc(a.text)}">⚠ ${esc(a.title)}</div>`).join('')}`;
  if (sta) sta.textContent = alerts.length ? `⚠ ${alerts.length}` : '';
}

// 头条背景行：FOMC 倒计时 · 衰退概率（Polymarket）· 今日研判页入口。
// 跨资产变化表/情景卡本体在 /daily/（同端点同 11 行，仪表盘不复制一遍）。
// 静态 JSON 冻结了构建期日历：FOMC 按打开页面时间重选下一场（R.nextMeeting 单源）；
// 天数口径统一 end_day + ceil。分隔点由 CSS ::before 统一画。
function renderBriefLink() {
  const box = document.getElementById('dash-hero-bg');
  if (!box) return;
  const d = data.brief;
  if (d.status !== 'fulfilled') { box.innerHTML = ''; return; }
  const v = d.value;
  const parts = [];
  const nx = R.nextMeeting(v.fomc);
  if (nx) {
    const days = Math.ceil((new Date(nx.year, nx.month - 1, nx.end_day) - new Date()) / 86400000);
    const next = `${nx.year}-${String(nx.month).padStart(2, '0')}-${String(nx.end_day).padStart(2, '0')}`;
    parts.push(`<span>FOMC ${next}${days > 0 ? `（${days} 天后）` : '（进行中）'}</span>`);
  }
  const pm = v.polymarket?.recession;
  if (pm?.prob != null) {
    const pct = (pm.prob * 100).toFixed(1).replace(/\.0$/, '');
    const y = (pm.end_date || '').slice(0, 4);
    parts.push(`<span title="${esc(pm.title)}（Polymarket ${esc(v.polymarket.as_of || '')}）">衰退 ${y} ${pct}%</span>`);
  }
  const n = (v.indicators?.rows || []).length;
  parts.push(`<a href="${SITE_NAV.home.page}" target="_blank">跨资产变化表 · 情景与反证${n ? `（${n} 个指标）` : ''} ↗</a>`);
  box.innerHTML = parts.join('');
}

// 头条背景行的数据截至段（多源口径只能由 R.asOf 组装，AGENTS 第 8 节）
function renderAsOf() {
  const elx = document.getElementById('dash-asof');
  if (!elx) return;
  const d = data.brief;
  if (d.status !== 'fulfilled') { elx.textContent = '数据不可用（brief 端点未响应，确认服务已启动）'; return; }
  const g = d.value.indicators?.groups || {};
  const segs = Object.entries(g).map(([k, v]) => [k, v]);
  elx.textContent = R.asOf(segs.length ? segs : [d.value.indicators?.as_of]);
}

// 今日一句话（叙事列）：文案全来自 Python 规则引擎，前端只渲染不计算
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
    box.innerHTML = '<div class="dash-line-empty">' + why
      + '，暂无当日研判一句话。先拉数据（如 <code>./bin/fetch_fred</code>），再跑 '
      + '<code>uv run python -m src.export_pages</code> 生成静态 JSON（本地服务同一路径）。'
      + '</div>';
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
  box.innerHTML = `
    <div class="dash-line-cards">${cards.map(l => lineCard(l, hot.has(l.key))).join('')}</div>
    ${chips.length ? `<div class="dash-calm"><span class="dash-calm-k">平稳</span>${chips.map(calmChip).join('<span class="dash-calm-dot" aria-hidden="true">·</span>')}</div>` : ''}`;
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

// 平稳专题只留弱化文字链：正文今天没有可说的，细节进专题页看
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

// 波动率：hero 4 卡（KPI 卡行单源 R.cards，列内 2×2）+ 信号列表（无卡片框）
function renderVol() {
  const sec = root().querySelector('.dash-vol-sec');
  const kpi = sec?.querySelector('.dash-kpi');
  const elx = sec?.querySelector('.dash-vol');
  const res = data.vol;
  if (!kpi || !elx) return;
  if (res.status !== 'fulfilled' || !res.value?.hero) {
    if (sec) sec.style.display = 'none';
    const stx = document.getElementById('dash-sticky-vix');
    if (stx) stx.textContent = '';
    return;
  }
  if (sec) sec.style.display = '';
  const v = res.value;
  kpi.replaceChildren(R.cards((v.hero.cards || []).map(c => ({
    label: c.symbol,
    value: fmtNum(c.value),
    // 「1D」口径已在节头注明，卡内不重复
    sub: `${esc(c.name)} · ${R.chgSpan(c.chg1d)}`,
  })), 'dash-vol-kpi-row'));
  const signals = (v.signals || []).map(s => `
    <div class="dash-sig">
      <div class="dash-sig-title">${esc(s.title)}</div>
      <div class="dash-sig-text">${esc(s.text)}</div>
      ${s.advice ? `<div class="dash-sig-advice">应对：${esc(s.advice)}</div>` : ''}
    </div>`).join('');
  elx.innerHTML = signals ? `<div class="dash-sig-list">${signals}</div>` : '';
  const stx = document.getElementById('dash-sticky-vix');
  if (stx) {
    const vix = (v.hero.cards || []).find(c => c.symbol === 'VIX');
    stx.textContent = vix ? `VIX ${fmtNum(vix.value)}` : '';
  }
}

// 市场快照：六板块进一张表，板块名做行内节头；每板块前 4 行 + 页脚行（v5 等高逻辑
// 在分栏表里的延续：行数对齐 + 全量进专题）
function renderAssetSnapshots() {
  const table = root().querySelector('.dash-snap-table');
  const res = data.assets;
  if (!table) return;
  if (res.status !== 'fulfilled' || !res.value?.tables) {
    table.innerHTML = '<div class="loading">资产快照加载失败 · 运行 ./bin/fetch_yfinance 后刷新</div>';
    return;
  }
  const boards = [
    ['equities', '股指'], ['bonds', '债券'], ['commodities', '商品'],
    ['etfs', 'ETF'], ['crypto', '加密'], ['fx', '外汇'],
  ];
  const MAX_ROWS = 4;
  table.innerHTML = boards.map(([key, label]) => {
    const rows = res.value.tables[key] || [];
    if (!rows.length) return '';
    const page = NAV_PAGES.get(`assets/${key}`);
    const title = page ? `<a class="dash-board-t" href="${page}" target="_blank">${label}</a>` : `<span class="dash-board-t">${label}</span>`;
    const shown = rows.slice(0, MAX_ROWS);
    const more = page
      ? `<a class="dash-board-more" href="${page}" target="_blank">${rows.length > MAX_ROWS ? `全部 ${rows.length} 项` : '进入专题'} →</a>` : '';
    return `<div class="dash-board">
      <div class="dash-board-head">${title} <small class="dash-title-note">日涨跌 · ${rows[0]?.date ?? ''}</small></div>
      <div class="dash-snap">${shown.map(r => {
        const cls = r.chg_pct == null || r.chg_pct === 0 ? 'neutral' : (r.chg_pct > 0 ? 'up' : 'down');
        const sign = r.chg_pct > 0 ? '+' : '';
        return `<div class="dash-snap-row">
          <span class="dash-snap-name">${esc(r.name)}</span>
          <span class="dash-snap-val">${fmtNum(r.last)}</span>
          <span class="${cls}">${r.chg_pct == null ? '—' : sign + r.chg_pct.toFixed(2) + '%'}</span>
        </div>`;
      }).join('')}</div>${more}</div>`;
  }).join('');
}

// 自选清单 chips（localStorage 持久化；标的来自 /api/symbols + /api/kline）
async function refreshWatch() {
  const chips = root().querySelector('#dash-watch-chips');
  const sel = root().querySelector('#dash-watch-add');
  if (!chips) return;
  let list;
  try { list = JSON.parse(localStorage.getItem(WATCH_KEY)) || DEFAULT_WATCH; }
  catch { list = DEFAULT_WATCH; }
  const syms = (await settled('/api/symbols'));
  const all = (syms.status === 'fulfilled' ? syms.value : []).map(s => s.name ?? s);
  const valid = list.filter(s => all.includes(s));
  const results = await Promise.all(
    valid.map(s => settled(`/api/kline/${s}?days=5`))
  );
  chips.innerHTML = valid.length ? valid.map((sym, i) => watchChip(sym, results[i])).join('')
    : '<span class="dash-watch-empty">暂无自选，从上方下拉添加</span>';
  if (sel) {
    sel.innerHTML = '<option value="">＋添加…</option>'
      + all.filter(s => !valid.includes(s)).map(s => `<option>${s}</option>`).join('');
    sel.onchange = e => {
      if (!e.target.value) return;
      valid.push(e.target.value);
      localStorage.setItem(WATCH_KEY, JSON.stringify(valid));
      refreshWatch();
    };
  }
  // chip 点击进技术分析；× 删除（触屏上 × 常显）
  chips.querySelectorAll('.dash-watch-x').forEach(x => {
    x.addEventListener('click', e => {
      e.stopPropagation();
      const sym = x.dataset.watchDel;
      const next = valid.filter(s => s !== sym);
      localStorage.setItem(WATCH_KEY, JSON.stringify(next));
      refreshWatch();
    });
  });
  chips.querySelectorAll('[data-go-stock]').forEach(chipEl => {
    chipEl.addEventListener('click', e => {
      if (e.target.closest('.dash-watch-x')) return;
      window.dispatchEvent(new CustomEvent('go-stock', { detail: chipEl.dataset.goStock }));
    });
    // 纯 div chip 键盘进不去：tabindex + Enter/Space 转发
    chipEl.tabIndex = 0;
    chipEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chipEl.click(); }
    });
  });
}

function watchChip(sym, r) {
  const ok = r && r.status === 'fulfilled' && Array.isArray(r.value) && r.value.length >= 2;
  const price = ok ? fmtNum(r.value[r.value.length - 1].close, 2) : '--';
  let pct = null;
  if (ok) {
    const arr = r.value;
    const prev = arr[arr.length - 2].close;
    if (prev) pct = (arr[arr.length - 1].close - prev) / prev * 100;
  }
  const pctHtml = pct == null ? '<span class="neutral">--</span>'
    : `<span class="${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span>`;
  return `<div class="dash-watch-chip" data-go-stock="${sym}" title="进入 ${sym} 技术分析">
    <b class="dash-watch-chip-sym">${sym}</b>
    <span class="dash-watch-chip-price">${price}</span>
    ${pctHtml}
    <button class="dash-watch-x" data-watch-del="${sym}" title="删除 ${sym}" aria-label="删除 ${sym}">×</button>
  </div>`;
}

// ── sticky 细条（仅 <1024px 显示）────────────────────────────
function buildSticky() {
  const bar = el('div', 'dash-sticky');
  bar.id = 'dash-sticky';
  bar.innerHTML = '<div class="dash-sticky-in"><span id="dash-sticky-verdict"></span><span id="dash-sticky-alert"></span><span id="dash-sticky-vix"></span></div>';
  return bar;
}

// 滚过头条后滑入（IntersectionObserver 以 .dashboard-view 为滚动根）
function setupStickyObserver(bar) {
  const hero = root().querySelector('.dash-hero');
  if (!hero || !('IntersectionObserver' in window)) return;
  const io = new IntersectionObserver(
    ([e]) => bar.classList.toggle('show', !e.isIntersecting),
    { root: hero.closest('.dashboard-view'), threshold: 0 },
  );
  io.observe(hero);
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
