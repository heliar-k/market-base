// tech-view.js — 技术分析视图：结论总览（默认） + K 线与指标图

import { CHART_OPTS, darkChartOpts, addLine, cssVar } from './charts-common.js';

// 转义单源 R.esc（rates-common.js 已在 app/index.html 先于本模块加载；注意顶层 const R 属于全局词法作用域，不挂在 window.R 上）
const esc = R.esc;
const app = document.querySelector('.app');

// ── state ──────────────────────────────────────────────────────────────────
const SUBVIEW_KEY = 'tech-subview';
let currentSubview = localStorage.getItem(SUBVIEW_KEY) === 'kline' ? 'kline' : 'overview';
let currentSymbol = null;
let currentDiag = null;
let overviewList = null; // /api/tech/overview 缓存
let overviewState = 'loading'; // loading | ready | error（决定切换标的是否还要回源 /api/diag）
let activeFilter = 'all'; // all | bullish | bearish | squeeze | oversold | overbought
let klineData = null; // full dataset from API
let filteredData = null; // after range filter
let klineSymbol = null; // klineData 属于哪个标的（防止切标的后仍拿旧 K 线画图/报报价）
let klineRequestSeq = 0; // 快速连点：只接受最后一次请求的响应
const klineCache = new Map(); // symbol → records（会话内复用，切回已看过的标的零请求）
const klinePending = new Map(); // symbol → Promise（同一标的连点只发一次请求）
const KLINE_CACHE_MAX = 4;
const KLINE_LOADING_TEXT = '正在加载 K 线数据…';
const KLINE_ERROR_TEXT = 'K 线数据加载失败 · 请检查 /api/kline 或刷新页面重试';
let currentDays = 365;
let mainChart, rsiChart, macdChart;
let candleSeries, volumeOverlaySeries;
let maSeries = {};
let rsiSeries, rsiUpper, rsiLower;
let macdHistSeries, macdLineSeries, macdSignalSeries;
let crosshairDate = null;
let debounceTimer = null;
// 调色板全部回读 tokens.css（每次调用重取，主题切换后生效）；键名与 MA 列名对应
const MA_COLORS = () => ({
  MA5: cssVar('--chart-cyan'),
  MA10: cssVar('--color-brand'),
  MA20: cssVar('--color-warn'),
  MA50: cssVar('--color-up'),
  MA60: cssVar('--chart-purple'),
  MA120: cssVar('--color-neutral'),
  MA200: cssVar('--color-down'),
});
const BB_COLOR = () => cssVar('--chart-slate');

// type=stock 但语义是工具型 ETF：下拉框归到「指数 & ETF」组，扫描表标 ETF 而非 指数
const ETF_LIKE = new Set([
  'SPY',
  'QQQ',
  'TLT',
  'LQD',
  'HYG',
  'GLD',
  'SLV',
  'USO',
  'DBA',
  'IBIT',
  'SMH',
  'IWM',
  'XLK',
  'XLE',
  'XLF',
  'XLV',
]);

// ── helpers ────────────────────────────────────────────────────────────────
function scoreTokens(score) {
  if (score > 3) return { text: 'var(--color-up-text)', fill: 'var(--color-up)' };
  if (score < -3) return { text: 'var(--color-down-text)', fill: 'var(--color-down)' };
  return { text: 'var(--color-warn-text)', fill: 'var(--color-warn)' };
}

function rsiToneCls(rsi, neutralCls = '') {
  if (rsi == null) return neutralCls;
  return rsi < 30 ? 'up' : rsi > 70 ? 'down' : neutralCls;
}

function regimeBadgeCls(regime) {
  if (regime === 'bullish' || regime === 'mild_bullish') return 'tag tag-bull';
  if (regime === 'bearish' || regime === 'mild_bearish') return 'tag tag-bear';
  return 'tag tech-ov-tag-neutral';
}

function fmtSigned(v, nd = 0, suffix = '') {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${R.num(n, nd)}${suffix}`;
}

function fmtVolume(v) {
  if (v == null) return '--';
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(v);
}

// 扫描表筛选谓词单源（filterOverviewItems 与 counts 统计共用；超买/超卖与后端 osc_oversold 30/70 对齐）
const SCAN_PREDICATES = {
  all: () => true,
  bullish: (d) => d.regime === 'bullish' || d.regime === 'mild_bullish',
  bearish: (d) => d.regime === 'bearish' || d.regime === 'mild_bearish',
  squeeze: (d) => Boolean(d.BB_squeeze),
  oversold: (d) => (d.RSI != null && d.RSI < 30) || d.STOCH_detail === 'oversold' || d.SMC_pd_zone === 'discount',
  overbought: (d) => (d.RSI != null && d.RSI > 70) || d.STOCH_detail === 'overbought' || d.SMC_pd_zone === 'premium',
};

// ── init ───────────────────────────────────────────────────────────────────
export function initTechView() {
  initSubviewSwitch();
  initCharts();
  initControls();
  initKeyboard();
  applySubview(currentSubview, false);
  loadSymbols();
  loadTechOverview();
}

// ── subview switch (overview / kline) ──────────────────────────────────────
function initSubviewSwitch() {
  document.querySelectorAll('#tech-subview-switch [data-subview]').forEach((btn) => {
    btn.addEventListener('click', () => applySubview(btn.dataset.subview, true));
  });
}

function resizeAllCharts() {
  requestAnimationFrame(() => {
    [mainChart, rsiChart, macdChart].forEach((c, i) => {
      const el = document.getElementById(['main-chart', 'rsi-chart', 'macd-chart'][i]);
      if (c && el && el.clientWidth > 0 && el.clientHeight > 0) {
        c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      }
    });
    if (mainChart && klineSymbol === currentSymbol && filteredData && filteredData.length) {
      mainChart.timeScale().fitContent();
    }
  });
}

function applySubview(subview, persist = true) {
  currentSubview = subview === 'kline' ? 'kline' : 'overview';
  if (persist) localStorage.setItem(SUBVIEW_KEY, currentSubview);

  document.querySelectorAll('#tech-subview-switch [data-subview]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.subview === currentSubview);
  });

  const ovPane = document.getElementById('tech-ov-pane');
  const klPane = document.getElementById('tech-kline-pane');
  if (ovPane) ovPane.style.display = currentSubview === 'overview' ? 'flex' : 'none';
  if (klPane) klPane.style.display = currentSubview === 'kline' ? 'flex' : 'none';

  if (currentSubview === 'kline') {
    // K 线数据按需加载：结论总览里切换标的只读 /api/tech/overview 缓存，不再每次拖整段历史
    ensureKline(currentSymbol);
    resizeAllCharts();
  }
}

// ── info bar & symbol selector ─────────────────────────────────────────────
// 当前标的的 K 线（未加载 / 刚切标的时返回 null，报价回退到诊断快照）
function activeKlineData() {
  return klineSymbol === currentSymbol ? filteredData || klineData : null;
}

// 总览里的诊断快照（每项就是 analyze() 完整输出，与 /api/diag 同源同字段）
function overviewDiag(symbol) {
  return overviewList?.find((d) => d.symbol === symbol) || null;
}

function updateInfoBar() {
  const data = activeKlineData();
  // 标的名不在此条重复：左侧下拉框已显示当前标的
  const price = document.getElementById('info-price');
  const change = document.getElementById('info-change');
  const ohlc = document.getElementById('info-ohlc');
  if (!price) return;
  if (!data || !data.length) {
    price.textContent = currentDiag?.last_price != null ? currentDiag.last_price.toFixed(2) : '--';
    const chg1d = currentDiag?.changes?.['1d'];
    if (chg1d != null) {
      const up = chg1d >= 0;
      change.textContent = `${up ? '▲' : '▼'} ${up ? '+' : ''}${Number(chg1d).toFixed(2)}%`;
      change.className = 'tech-change ' + (up ? 'up' : 'down');
    } else {
      change.textContent = '--';
      change.className = 'tech-change';
    }
    ohlc.textContent = currentDiag?.regime_label
      ? `${currentDiag.regime_label} · ${currentDiag.setup_tag || ''}`
      : '--';
    return;
  }
  const last = data[data.length - 1];
  const prev = data.length >= 2 ? data[data.length - 2] : last;
  const chg = prev.close ? ((last.close - prev.close) / prev.close) * 100 : 0;
  const up = chg >= 0;
  price.textContent = last.close.toFixed(2);
  change.textContent = `${up ? '▲' : '▼'} ${up ? '+' : ''}${chg.toFixed(2)}%`;
  change.className = 'tech-change ' + (up ? 'up' : 'down');
  ohlc.textContent = `O:${last.open.toFixed(2)} H:${last.high.toFixed(2)} L:${last.low.toFixed(2)} V:${fmtVolume(last.volume)}`;
}

async function loadSymbols() {
  const sel = document.getElementById('tech-sym-select');
  let symbols;
  try {
    const res = await fetch('/api/symbols');
    if (!res.ok) throw new Error(res.statusText);
    symbols = await res.json();
  } catch {
    if (sel) sel.innerHTML = '<option value="">加载失败</option>';
    return;
  }

  const indices = symbols.filter((s) => s.type === 'index' || ETF_LIKE.has(s.name));
  const stocks = symbols.filter((s) => s.type === 'stock' && !ETF_LIKE.has(s.name));

  if (sel) {
    const mkGroup = (label, list) =>
      `<optgroup label="${esc(label)}">${list.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('')}</optgroup>`;
    sel.innerHTML = mkGroup('指数 & ETF', indices) + mkGroup('个股', stocks);
    sel.addEventListener('change', () => {
      if (sel.value) selectSymbol(sel.value);
    });
  }

  // 默认选中第一个标的（优先 SPX / AAPL / 首项）
  if (!currentSymbol && symbols.length) {
    const def = symbols.find((s) => s.name === 'SPX') || symbols.find((s) => s.name === 'AAPL') || symbols[0];
    selectSymbol(def.name);
  }
}

export async function selectSymbol(name) {
  currentSymbol = name;
  const sel = document.getElementById('tech-sym-select');
  if (sel && sel.value !== name) sel.value = name;
  const activeRow = document.querySelector(`.tech-ov-row[data-symbol="${CSS.escape(name)}"]`);
  document.querySelectorAll('.tech-ov-row.active').forEach((el) => {
    if (el !== activeRow) el.classList.remove('active');
  });
  activeRow?.classList.add('active');
  document.getElementById('status-symbol').textContent = name;

  // 若总览已缓存该标的，立即先渲染深度结论卡（零等待）；没有就先清空，
  // 避免报价条停留在上一个标的的数字上
  const cached = overviewDiag(name);
  currentDiag = cached || null;
  if (cached) {
    renderCurrentConclusion(cached);
  } else if (overviewState !== 'loading') {
    // 总览失败/缺该标的时才回源；总览仍在加载就等它回来统一渲染，避免白跑一次 /api/diag
    fetchDiag();
  }
  updateInfoBar();

  if (currentSubview === 'kline') {
    await ensureKline(name);
    return;
  }

  // 结论总览不预取 K 线：结论已在 overview 缓存里，没必要为切标的拉整段历史
  // （即使收窄并压缩后，仍是一段纯网络等待）。这里清掉上一个标的的图表数据，报价走 overview 快照。
  klineRequestSeq += 1;
  klineData = null;
  filteredData = null;
  klineSymbol = null;
  updateInfoBar();
  updateStatus();
}

// 按需拉取 / 渲染 K 线：结论总览切换标的完全不碰这个接口；
// 进入 K 线子视图或已在 K 线视图里切标的时才调用，并按会话缓存复用。
async function ensureKline(name) {
  if (!name) return;
  // 每次进入/切换都推进序号：即使命中缓存，也要让上一只标的的在飞请求失效，
  // 否则它返回时会从早退分支溜走，把「正在加载…」永远留在页面上。
  const seq = ++klineRequestSeq;

  if (klineSymbol === name && klineData) {
    setKlineLoading(false);
    applyRangeFilter();
    updateInfoBar();
    if (currentSubview === 'kline') renderCharts();
    return;
  }

  // 图表/状态栏还留着上一只标的的蜡烛与区间：先清空，避免标签已换、数据未换
  if (klineSymbol !== name) {
    clearCharts();
    updateStatus();
  }

  let data = klineCache.get(name);
  if (data) {
    // LRU：命中后移到队尾
    klineCache.delete(name);
    klineCache.set(name, data);
  } else {
    setKlineLoading(true);
    // 同一标的的连点/总览切 K 线重复进入只复用同一个请求
    let pending = klinePending.get(name);
    if (!pending) {
      pending = fetch(`/api/kline/${name}`).then((res) => {
        if (!res.ok) throw new Error(res.statusText);
        return res.json();
      });
      klinePending.set(name, pending);
      pending.then(
        () => klinePending.delete(name),
        () => klinePending.delete(name)
      );
    }
    try {
      data = await pending;
    } catch {
      if (seq === klineRequestSeq) setKlineError();
      updateInfoBar();
      return;
    }
    klineCache.set(name, data);
    if (klineCache.size > KLINE_CACHE_MAX) {
      klineCache.delete(klineCache.keys().next().value);
    }
  }

  // 快速连点/已切到别的标的：丢弃过期响应（缓存已留，下次点回来仍零请求）
  if (seq !== klineRequestSeq || currentSymbol !== name) return;

  klineData = data;
  klineSymbol = name;
  applyRangeFilter();
  updateInfoBar();
  if (currentSubview === 'kline') renderCharts();
  setKlineLoading(false);
}

function setKlineLoading(loading) {
  const el = document.getElementById('kline-loading');
  if (!el) return;
  el.textContent = KLINE_LOADING_TEXT;
  el.hidden = !loading;
}

// 加载失败不能只把提示藏起来留三个空图：按 AGENTS §8 给原因与下一步
function setKlineError() {
  const el = document.getElementById('kline-loading');
  if (!el) return;
  el.textContent = KLINE_ERROR_TEXT;
  el.hidden = false;
}

// ── 结论总览：全标的扫描 + 当前标的深度结论卡 ──────────────────────────────
async function loadTechOverview() {
  const scanEl = document.getElementById('tech-ov-scan');
  if (scanEl) scanEl.innerHTML = '<div class="tech-ov-card"><div class="loading">正在扫描全市场技术面信号…</div></div>';
  overviewState = 'loading';
  try {
    const data = await R.getOpt('/api/tech/overview');
    if (!Array.isArray(data) || !data.length) throw new Error('empty overview');
    overviewList = data;
    overviewState = 'ready';
    renderScanSection();
    if (currentSymbol && !currentDiag) {
      const hit = overviewDiag(currentSymbol);
      if (hit) {
        currentDiag = hit;
        renderCurrentConclusion(hit);
        updateInfoBar();
      } else {
        // 总览里没有该标的（数据缺行）：退回 /api/diag，避免结论卡空着
        fetchDiag();
      }
    }
  } catch {
    overviewState = 'error';
    if (scanEl) {
      scanEl.innerHTML =
        '<div class="tech-ov-card"><div class="sidebar-watch-empty">全市场技术扫描暂不可用 · 请确认服务已启动（uv run python -m src.server）或已运行 uv run python -m src.export_pages</div></div>';
    }
    // 总览挂了也要让当前标的的结论卡有数据
    if (currentSymbol && !currentDiag) fetchDiag();
  }
}

function renderCurrentConclusion(d) {
  const host = document.getElementById('tech-ov-current');
  if (!host || !d) return;

  const chg1d = d.changes?.['1d'];
  const chgHtml = chg1d != null ? R.chgSpan(chg1d) : '';
  const asOfText = R.asOf(d.last_date || '');

  const lines =
    Array.isArray(d.summary_lines) && d.summary_lines.length ? d.summary_lines : [d.summary_text || '暂无研判结论'];
  const lineLabels = R.LINES_CONCLUSION;

  const scoreBreak = (title, total, items) => `
    <div class="tech-ov-dim-col">
      <div class="tech-ov-dim-head">
        <span>${esc(title)}</span>
        <span class="diag-value ${total > 0 ? 'up' : total < 0 ? 'down' : 'neutral'}">${fmtSigned(total)}</span>
      </div>
      ${
        (items || [])
          .map(
            (s) => `
        <div class="diag-row">
          <span class="diag-label">${esc(s.label)}</span>
          <span class="diag-value ${s.value > 0 ? 'up' : s.value < 0 ? 'down' : 'neutral'}">${fmtSigned(s.value)}</span>
        </div>
      `
          )
          .join('') || '<div class="sidebar-watch-empty">无触发项</div>'
      }
    </div>
  `;

  host.innerHTML = '';

  // 1) 四张核心 KPI 卡作为顶层独立卡行（不塞进 .tech-ov-card，避免卡中卡双层框）
  const kpis = R.cards([
    {
      label: '综合评分 / 状态',
      value: `${fmtSigned(d.total_score)} · ${esc(d.regime_label || '—')}`,
      sub: `趋势 ${fmtSigned(d.trend_score)} · 动量 ${fmtSigned(d.momentum_score)} · 位置 ${fmtSigned(d.loc_score)}`,
      vcolor: scoreTokens(d.total_score).text,
      cls: 're-card-dense',
    },
    {
      label: '核心形态 / 信号',
      value: esc(d.setup_tag || d.regime_label || '—'),
      sub: `ADX ${d.ADX?.toFixed(1) ?? '—'} · RSI ${d.RSI?.toFixed(1) ?? '—'} · ${d.MACD_status === 'golden_cross' ? 'MACD金叉' : 'MACD死叉'}`,
      cls: 're-card-dense re-card-wrap',
    },
    {
      label: '关键支撑 / 阻力 (90D)',
      value: `$${d.support_90d?.toFixed(2) ?? '—'} — $${d.resistance_90d?.toFixed(2) ?? '—'}`,
      sub: `MA50 $${d.ma_values?.MA50?.toFixed(2) ?? '—'} (${fmtSigned(d.ma_dists?.MA50, 1, '%')}) · MA200 $${d.ma_values?.MA200?.toFixed(2) ?? '—'} (${fmtSigned(d.ma_dists?.MA200, 1, '%')})`,
      cls: 're-card-dense',
    },
    {
      label: '52 周区间分位 / 波幅',
      value: d.pos_52w != null ? `${d.pos_52w.toFixed(1)}%` : '—',
      sub: `52W $${d.low_52w?.toFixed(2) ?? '—'}–$${d.high_52w?.toFixed(2) ?? '—'} · ATR ${d.ATR_pct != null ? d.ATR_pct.toFixed(1) + '%' : '—'}${d.BB_width != null ? ` · 带宽 ${d.BB_width}%` : ''}`,
      cls: 're-card-dense',
    },
  ]);
  host.appendChild(kpis);

  // 2) 深度结论与三维依据明细卡
  const card = document.createElement('div');
  card.className = 'tech-ov-card';

  const head = document.createElement('div');
  head.className = 'tech-ov-head';
  head.innerHTML = `
    <div class="tech-ov-title-wrap">
      <span class="tech-ov-sym">${esc(d.symbol)}</span>
      <span class="tech-ov-price">${d.last_price != null ? '$' + d.last_price.toFixed(2) : '—'}</span>
      ${chgHtml}
      <span class="${regimeBadgeCls(d.regime)}">${esc(d.regime_label || '—')}</span>
      ${d.setup_tag && d.setup_tag !== d.regime_label ? `<span class="tag tech-ov-tag-setup">${esc(d.setup_tag)}</span>` : ''}
      ${d.BB_squeeze ? '<span class="tag tech-ov-tag-warn">Squeeze 压缩</span>' : ''}
      <span class="tech-ov-date">${esc(asOfText)}</span>
    </div>
    <button type="button" class="range-btn" id="tech-ov-go-kline">查看 K 线与指标图 →</button>
  `;
  head.querySelector('#tech-ov-go-kline')?.addEventListener('click', () => applySubview('kline', true));
  card.appendChild(head);

  // 三行结论（结论 / 依据 / 触发）
  const summaryBox = document.createElement('div');
  summaryBox.className = 'tech-ov-summary';
  summaryBox.innerHTML = lines
    .map(
      (ln, idx) => `
    <div class="tech-ov-summary-item">
      <div class="tech-ov-summary-label">${esc(lineLabels[idx] || '要点')}</div>
      <p class="tech-ov-summary-text">${esc(ln)}</p>
    </div>
  `
    )
    .join('');
  card.appendChild(summaryBox);

  // 三维评分依据明细（趋势 / 动量 / 位置与反转）
  const dimGrid = document.createElement('div');
  dimGrid.className = 'tech-ov-dim-grid';
  dimGrid.innerHTML =
    scoreBreak('趋势维度 (Trend)', d.trend_score, d.trend_scores) +
    scoreBreak('动量维度 (Momentum)', d.momentum_score, d.momentum_scores) +
    scoreBreak('位置与反转 (Location)', d.loc_score, d.loc_scores);
  card.appendChild(dimGrid);

  // 辅助震荡与阶段涨跌快照（原右侧栏指标合入此处，CCI 仅展示不计分）
  if (R.kvRows) {
    const auxItems = [
      {
        label: 'Stoch K / D',
        note: d.STOCH_detail || '',
        value: `${d.STOCH_k?.toFixed(1) ?? '—'} / ${d.STOCH_d?.toFixed(1) ?? '—'}`,
      },
      { label: 'CCI（仅展示不计分）', note: d.CCI_detail || '', value: d.CCI != null ? d.CCI.toFixed(1) : '—' },
      { label: 'MFI 资金流', value: d.MFI != null ? d.MFI.toFixed(1) : '—' },
      { label: 'OBV 量价背离', value: esc(d.OBV_divergence || '无') },
      { label: '量比 (vs MA20)', value: d.vol_ratio != null ? `${d.vol_ratio.toFixed(2)}x` : '—' },
      {
        label: '阶段涨跌 (5D / 1M / 3M)',
        value: ['5d', '1m', '3m']
          .map((k) => (d.changes?.[k] != null ? fmtSigned(d.changes[k], 1, '%') : '—'))
          .join(' / '),
      },
    ];
    card.appendChild(R.kvRows(auxItems));
  }

  card.appendChild(
    R.secNote(
      '评分口径：趋势（MA20/50/200 排列、MA200 牛熊线、ADX、SuperTrend、周/日 SMC 结构）+ 动量（MACD 金死叉与柱体、RSI 动量带、OBV 背离）+ 位置反转（RSI/Stoch 去重超买超卖、MFI 背离、50 日折溢价区、流动性扫荡、关键 K 线形态）。点击下方任意标的切换结论，双击或点行末「K线」直达图表。'
    )
  );

  host.appendChild(card);
}

function filterOverviewItems(items) {
  if (!Array.isArray(items)) return [];
  const pred = SCAN_PREDICATES[activeFilter] || SCAN_PREDICATES.all;
  return items.filter(pred);
}

function renderScanSection() {
  const host = document.getElementById('tech-ov-scan');
  if (!host || !Array.isArray(overviewList)) return;

  const counts = Object.fromEntries(
    Object.entries(SCAN_PREDICATES).map(([k, fn]) => [k, overviewList.filter(fn).length])
  );

  const filters = [
    { key: 'all', label: `全部 (${counts.all})` },
    { key: 'bullish', label: `多头/偏多 (${counts.bullish})` },
    { key: 'bearish', label: `空头/偏空 (${counts.bearish})` },
    { key: 'squeeze', label: `Squeeze压缩 (${counts.squeeze})` },
    { key: 'oversold', label: `超卖/折价区 (${counts.oversold})` },
    { key: 'overbought', label: `超买/溢价区 (${counts.overbought})` },
  ];

  // 按综合分降序排列，便于一眼扫出最强与最弱
  const rows = [...filterOverviewItems(overviewList)].sort((a, b) => (b.total_score ?? 0) - (a.total_score ?? 0));

  host.innerHTML = '';
  const card = document.createElement('div');
  card.className = 'tech-ov-card';

  const toolbar = document.createElement('div');
  toolbar.className = 'tech-ov-scan-bar';
  toolbar.innerHTML = `
    <div class="tech-ov-scan-title">全标的信号扫描 <span class="tech-ov-date">共 ${overviewList.length} 标的 · 点击行查看结论，点「K线」切图表</span></div>
    <div class="tech-ov-filters">
      ${filters.map((f) => `<button type="button" class="range-btn${activeFilter === f.key ? ' active' : ''}" data-filter="${f.key}">${esc(f.label)}</button>`).join('')}
    </div>
  `;
  toolbar.querySelectorAll('[data-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeFilter = btn.dataset.filter;
      renderScanSection();
    });
  });
  card.appendChild(toolbar);

  const wrap = document.createElement('div');
  wrap.className = 'tech-ov-table-wrap';
  wrap.innerHTML = `
    <table class="tech-ov-table">
      <thead>
        <tr>
          <th scope="col">标的</th>
          <th scope="col" class="num">最新价</th>
          <th scope="col" class="num">1D 涨跌</th>
          <th scope="col">趋势状态</th>
          <th scope="col">核心形态 / Setup</th>
          <th scope="col" class="num">综合分</th>
          <th scope="col" class="num">趋势/动量/位置</th>
          <th scope="col" class="num">RSI(14)</th>
          <th scope="col" class="num">距 MA200</th>
          <th scope="col" class="num">52W 分位</th>
          <th scope="col" class="num">操作</th>
        </tr>
      </thead>
      <tbody>
        ${
          rows
            .map((d) => {
              const chg1d = d.changes?.['1d'];
              const dist200 = d.ma_dists?.MA200;
              const isAct = d.symbol === currentSymbol;
              return `
            <tr class="tech-ov-row${isAct ? ' active' : ''}" data-symbol="${esc(d.symbol)}">
              <td><strong>${esc(d.symbol)}</strong> <span class="tech-ov-type">${d.type === 'index' ? '指数' : ETF_LIKE.has(d.symbol) ? 'ETF' : ''}</span></td>
              <td class="num">${d.last_price != null ? '$' + d.last_price.toFixed(2) : '—'}</td>
              <td class="num ${chg1d != null ? (chg1d > 0 ? 'up' : chg1d < 0 ? 'down' : 'neutral') : ''}">${chg1d != null ? fmtSigned(chg1d, 2, '%') : '—'}</td>
              <td><span class="${regimeBadgeCls(d.regime)}">${esc(d.regime_label || '—')}</span></td>
              <td>
                <span class="tag tech-ov-tag-setup">${esc(d.setup_tag || '—')}</span>
                ${d.BB_squeeze && d.setup_tag !== '波动率压缩(Squeeze)' ? '<span class="tag tech-ov-tag-warn">Squeeze</span>' : ''}
              </td>
              <td class="num"><strong class="${d.total_score > 0 ? 'up' : d.total_score < 0 ? 'down' : 'neutral'}">${fmtSigned(d.total_score)}</strong></td>
              <td class="num tech-ov-tri">${fmtSigned(d.trend_score)} / ${fmtSigned(d.momentum_score)} / ${fmtSigned(d.loc_score)}</td>
              <td class="num ${rsiToneCls(d.RSI)}">${d.RSI != null ? d.RSI.toFixed(1) : '—'}</td>
              <td class="num ${dist200 != null ? (dist200 > 0 ? 'up' : dist200 < 0 ? 'down' : '') : ''}">${dist200 != null ? fmtSigned(dist200, 1, '%') : '—'}</td>
              <td class="num">${d.pos_52w != null ? d.pos_52w.toFixed(0) + '%' : '—'}</td>
              <td class="num"><button type="button" class="range-btn tech-ov-kbtn" data-go-kline="${esc(d.symbol)}">K线</button></td>
            </tr>
          `;
            })
            .join('') || '<tr><td colspan="11" class="sidebar-watch-empty">当前筛选条件下无匹配标的</td></tr>'
        }
      </tbody>
    </table>
  `;

  wrap.querySelectorAll('.tech-ov-row').forEach((tr) => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('[data-go-kline]')) return;
      const sym = tr.dataset.symbol;
      if (sym) selectSymbol(sym);
    });
    tr.addEventListener('dblclick', () => {
      const sym = tr.dataset.symbol;
      if (sym) {
        selectSymbol(sym);
        applySubview('kline', true);
      }
    });
  });

  wrap.querySelectorAll('[data-go-kline]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sym = btn.dataset.goKline;
      if (sym) {
        selectSymbol(sym);
        applySubview('kline', true);
      }
    });
  });

  card.appendChild(wrap);
  host.appendChild(card);
}

// ── range filter ───────────────────────────────────────────────────────────
function applyRangeFilter() {
  if (!klineData || !klineData.length) {
    filteredData = klineData;
    return;
  }
  if (currentDays === 0) {
    filteredData = klineData;
  } else {
    filteredData = klineData.slice(-currentDays);
  }
  document.getElementById('status-count').textContent = `${filteredData.length} 条数据`;
  if (filteredData.length > 0) {
    document.getElementById('status-range').textContent =
      `${filteredData[0].date} → ${filteredData[filteredData.length - 1].date}`;
  }
}

function initRangeControls() {
  document.querySelectorAll('#kline-range-controls .range-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#kline-range-controls .range-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentDays = Number(btn.dataset.range);
      applyRangeFilter();
      updateInfoBar();
      renderCharts();
    });
  });
}

// ── charts ─────────────────────────────────────────────────────────────────
let _chartContainers = []; // ResizeObserver targets

function initCharts() {
  const mainEl = document.getElementById('main-chart');
  const rsiEl = document.getElementById('rsi-chart');
  const macdEl = document.getElementById('macd-chart');

  mainChart = LightweightCharts.createChart(mainEl, {
    ...CHART_OPTS,
    width: mainEl.clientWidth || 600,
    height: mainEl.clientHeight || 260,
  });
  mainChart.priceScale('right').applyOptions({ autoScale: true, scaleMargins: { top: 0.1, bottom: 0.15 } });
  candleSeries = mainChart.addCandlestickSeries(candleColors());
  // ponytail: thin volume overlay on main chart for quick visual reference
  volumeOverlaySeries = mainChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
    color: volumeColor(),
  });
  mainChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: true });

  rsiChart = LightweightCharts.createChart(rsiEl, {
    ...CHART_OPTS,
    width: rsiEl.clientWidth || 600,
    height: rsiEl.clientHeight || 120,
  });
  rsiChart.priceScale('right').applyOptions({ minimum: 0, maximum: 100 });
  rsiSeries = rsiChart.addLineSeries({ color: cssVar('--chart-violet'), lineWidth: 1.5 });
  rsiUpper = rsiChart.addLineSeries({
    color: cssVar('--color-down'),
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  rsiLower = rsiChart.addLineSeries({
    color: cssVar('--color-up'),
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  macdChart = LightweightCharts.createChart(macdEl, {
    ...CHART_OPTS,
    width: macdEl.clientWidth || 600,
    height: macdEl.clientHeight || 120,
  });
  macdHistSeries = macdChart.addHistogramSeries({ priceFormat: { type: 'price', precision: 2 } });
  macdLineSeries = macdChart.addLineSeries({ color: cssVar('--color-brand'), lineWidth: 1.5 });
  macdSignalSeries = macdChart.addLineSeries({ color: cssVar('--color-warn'), lineWidth: 1.5 });

  // resize observer — auto-adapt when container size changes (window resize / subview switch)
  _chartContainers.forEach((ro) => ro?.disconnect());
  _chartContainers = [];
  const charts = [
    { el: mainEl, chart: mainChart },
    { el: rsiEl, chart: rsiChart },
    { el: macdEl, chart: macdChart },
  ];
  charts.forEach(({ el, chart }) => {
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        const h = e.contentRect.height;
        if (w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
      }
    });
    ro.observe(el);
    _chartContainers.push(ro);
  });

  // 主题热切换：布局 + 各 series 颜色都重新从当前主题 token 取，再重绘（renderCharts 内含 overlay/逐点色重算）
  window.addEventListener('theme-changed', () => {
    [mainChart, rsiChart, macdChart].forEach((c) => c.applyOptions(darkChartOpts()));
    applySeriesColors();
    renderCharts();
  });
  // 应用初始主题
  if (document.body.classList.contains('dark')) {
    const darkOpts = darkChartOpts();
    [mainChart, rsiChart, macdChart].forEach((c) => c.applyOptions(darkOpts));
  }

  // sync time scales across all 3 charts
  const allCharts = [mainChart, rsiChart, macdChart];
  const syncFrom = (src, targets) => {
    src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range) return;
      targets.forEach((t) => t.timeScale().setVisibleLogicalRange(range));
    });
  };
  allCharts.forEach((src) =>
    syncFrom(
      src,
      allCharts.filter((t) => t !== src)
    )
  );

  // crosshair sync
  // 副图十字光标同步：lightweight-charts v4.2.2 的签名是
  // setCrosshairPosition(price, horizontalPosition: time, seriesApi)，第三参数必须是
  // seriesApi 句柄（内部 xw.get(seriesApi) 查不到就静默 return）。
  // 原实现传 (NaN, NaN, param.time) 从未生效，这里改为 [图, 该图的 seriesApi, 数据列名]。
  const subCharts = [
    [rsiChart, rsiSeries, 'RSI'],
    [macdChart, macdHistSeries, 'MACD_hist'],
  ];
  mainChart.subscribeCrosshairMove((param) => {
    if (!param || !param.time) {
      crosshairDate = null;
      subCharts.forEach(([c]) => c.clearCrosshairPosition());
      return;
    }
    crosshairDate = param.time;
    const row = filteredData ? filteredData.find((d) => d.date === param.time) : null;
    subCharts.forEach(([c, s, key]) => {
      const v = row ? row[key] : null;
      if (v == null) c.clearCrosshairPosition();
      else c.setCrosshairPosition(v, param.time, s);
    });
    debouncedDiag();
  });
}

// ── 图表颜色：单源 tokens.css ───────────────────────────────────────────────
function candleColors() {
  const up = cssVar('--color-up'),
    down = cssVar('--color-down');
  return {
    upColor: up,
    downColor: down,
    borderUpColor: up,
    borderDownColor: down,
    wickUpColor: up,
    wickDownColor: down,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  };
}
// 量能柱：品牌蓝半透明（8 位十六进制后缀，承接原浅蓝量能色的语义位）
function volumeColor() {
  return cssVar('--color-brand') + '99';
}

// 主题切换后把新色应用到非逐点着色的 series（逐点色由 renderCharts/refreshOverlays 重算）
function applySeriesColors() {
  candleSeries.applyOptions(candleColors());
  volumeOverlaySeries.applyOptions({ color: volumeColor() });
  rsiSeries.applyOptions({ color: cssVar('--chart-violet') });
  rsiUpper.applyOptions({ color: cssVar('--color-down') });
  rsiLower.applyOptions({ color: cssVar('--color-up') });
  macdLineSeries.applyOptions({ color: cssVar('--color-brand') });
  macdSignalSeries.applyOptions({ color: cssVar('--color-warn') });
}

// 切标的时先清掉图上上一只的蜡烛（标签已换、图还没换会误导），新数据到达再重绘
function clearCharts() {
  if (!candleSeries) return;
  candleSeries.setData([]);
  volumeOverlaySeries.setData([]);
  rsiSeries.setData([]);
  rsiUpper.setData([]);
  rsiLower.setData([]);
  macdHistSeries.setData([]);
  macdLineSeries.setData([]);
  macdSignalSeries.setData([]);
  Object.values(maSeries).forEach((s) => {
    try {
      mainChart.removeSeries(s);
    } catch {}
  });
  maSeries = {};
  crosshairDate = null;
}

function renderCharts() {
  if (klineSymbol !== currentSymbol) return;
  const data = filteredData;
  if (!data || !data.length) return;
  // 逐点着色的颜色只取一次：cssVar 走 getComputedStyle，原先放在 map 里
  // 每根 K 线都调用一次（365 天 × 量能 + MACD ≈ 700+ 次），白白拖慢重绘。
  const up = cssVar('--color-up');
  const down = cssVar('--color-down');
  const upSoft = up + '4D';
  const downSoft = down + '4D';
  const candles = data.map((d) => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close }));
  const volumes = data.map((d) => ({
    time: d.date,
    value: d.volume ?? 0,
    color: d.close >= d.open ? upSoft : downSoft,
  }));
  candleSeries.setData(candles);
  volumeOverlaySeries.setData(volumes);

  // RSI
  const rsi = data.filter((d) => d.RSI != null).map((d) => ({ time: d.date, value: d.RSI }));
  rsiSeries.setData(rsi);
  if (rsi.length) {
    const times = rsi.map((d) => d.time);
    rsiUpper.setData(times.map((t) => ({ time: t, value: 70 })));
    rsiLower.setData(times.map((t) => ({ time: t, value: 30 })));
  }

  // MACD
  const hist = data
    .filter((d) => d.MACD_hist != null)
    .map((d) => ({
      time: d.date,
      value: d.MACD_hist,
      color: d.MACD_hist >= 0 ? up : down,
    }));
  const macdLine = data.filter((d) => d.MACD != null).map((d) => ({ time: d.date, value: d.MACD }));
  const signalLine = data.filter((d) => d.MACD_signal != null).map((d) => ({ time: d.date, value: d.MACD_signal }));
  macdHistSeries.setData(hist);
  macdLineSeries.setData(macdLine);
  macdSignalSeries.setData(signalLine);

  refreshOverlays();
  mainChart.timeScale().fitContent();
}

// ── overlays ───────────────────────────────────────────────────────────────
function initControls() {
  document.querySelectorAll('#controls input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener('change', refreshOverlays);
  });
  initRangeControls();
}

function refreshOverlays() {
  if (klineSymbol !== currentSymbol) return;
  const data = filteredData;
  if (!data) return;
  Object.values(maSeries).forEach((s) => {
    try {
      mainChart.removeSeries(s);
    } catch {}
  });
  maSeries = {};

  const active = [...document.querySelectorAll('#controls input[type="checkbox"]:checked')].map((c) => c.dataset.ma);

  active.forEach((key) => {
    if (key === 'BB') {
      const bb = BB_COLOR();
      const upper = data.filter((d) => d.BB_upper != null).map((d) => ({ time: d.date, value: d.BB_upper }));
      const lower = data.filter((d) => d.BB_lower != null).map((d) => ({ time: d.date, value: d.BB_lower }));
      const mid = data.filter((d) => d.BB_mid != null).map((d) => ({ time: d.date, value: d.BB_mid }));
      maSeries['BB_upper'] = addLine(mainChart, upper, bb, 1, 2);
      maSeries['BB_lower'] = addLine(mainChart, lower, bb, 1, 2);
      maSeries['BB_mid'] = addLine(mainChart, mid, bb, 1, 0);
    } else if (key === 'ST') {
      const stUp = data
        .filter((d) => d.SUPERT_dir === 1 && d.SUPERT != null)
        .map((d) => ({ time: d.date, value: d.SUPERT }));
      const stDn = data
        .filter((d) => d.SUPERT_dir === -1 && d.SUPERT != null)
        .map((d) => ({ time: d.date, value: d.SUPERT }));
      maSeries['ST_up'] = addLine(mainChart, stUp, cssVar('--color-up'), 2, 0);
      maSeries['ST_dn'] = addLine(mainChart, stDn, cssVar('--color-down'), 2, 0);
    } else {
      const d2 = data.filter((d) => d[key] != null).map((d) => ({ time: d.date, value: d[key] }));
      const width = key === 'MA200' ? 2 : 1.5;
      maSeries[key] = addLine(mainChart, d2, MA_COLORS()[key] || cssVar('--text-secondary'), width, 0);
    }
  });
}

// ── diagnosis ──────────────────────────────────────────────────────────────
function debouncedDiag() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchDiag, 200);
}

async function fetchDiag() {
  if (!currentSymbol) return;
  const sym = currentSymbol;
  // /api/tech/overview 每项就是 analyze() 的完整输出，与 /api/diag 同源同字段；
  // 总览已加载时直接复用，免掉切换标的后第二次回源 + 第二遍重绘结论卡。
  const cached = overviewDiag(sym);
  if (cached) {
    currentDiag = cached;
    if (currentSubview === 'overview') {
      renderCurrentConclusion(cached);
      updateInfoBar();
    }
    return;
  }
  // Pages 静态部署：as_of 光标回看无法预渲染所有日期，固定返回最新诊断
  const url = `/api/diag/${sym}`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    const d = await res.json();
    if (sym !== currentSymbol) return; // 请求期间已切走：不要用旧标的覆盖当前结论
    currentDiag = d;
    renderCurrentConclusion(d);
    updateInfoBar();
  } catch {
    // 已有 overview 缓存时保持当前展示
  }
}

// ── keyboard ───────────────────────────────────────────────────────────────
// 方向键回看的两道守卫：
// 1) 视图状态源 = app.js switchTab() 写入的 .app[data-view]（CSS 也靠它切换视图），
//    离开技术视图（仪表盘/宏观/关联）或不在 K 线子视图时不接管，否则页面滚动会被吞。
// 2) 焦点在可输入元素上（含 contenteditable / [role=combobox]）不接管，
//    否则搜索框、代码输入、select 里的方向键全部失效。
const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
function isEditableTarget(el) {
  if (!el || typeof el !== 'object') return false;
  if (EDITABLE_TAGS.has(el.tagName)) return true;
  if (el.isContentEditable) return true;
  return typeof el.getAttribute === 'function' && el.getAttribute('role') === 'combobox';
}

function initKeyboard() {
  document.addEventListener('keydown', (e) => {
    if (app.dataset.view !== 'tech' || currentSubview !== 'kline') return;
    if (isEditableTarget(e.target) || isEditableTarget(document.activeElement)) return;
    const data = activeKlineData();
    if (!data || !data.length) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const ts = mainChart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (!range) return;
      const totalBars = data.length;
      let idx = crosshairDate ? data.findIndex((d) => d.date === crosshairDate) : Math.floor(totalBars / 2);
      idx += e.key === 'ArrowRight' ? 1 : -1;
      idx = Math.max(0, Math.min(totalBars - 1, idx));
      crosshairDate = data[idx].date;
      // horizontalPosition 是 time（不是 bar index）：传 idx 会被当成 1970 年的时间戳，光标恒停在首根
      mainChart.setCrosshairPosition(data[idx].close, data[idx].date, candleSeries);
      debouncedDiag();
    }
  });
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = currentSymbol || '—';
  const data = activeKlineData();
  if (data && data.length) {
    document.getElementById('status-count').textContent = `${data.length} 条数据`;
    document.getElementById('status-range').textContent = `${data[0].date} → ${data[data.length - 1].date}`;
  } else {
    document.getElementById('status-count').textContent = '';
    document.getElementById('status-range').textContent = '';
  }
}
