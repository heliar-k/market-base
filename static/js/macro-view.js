// macro-view.js — 宏观引导页：左侧专题卡片 + 右侧详情 iframe（预加载缓存，无需跳转）

// ── 专题卡片 ────────────────────────────────────────────────────────────────
const FEATURED = [
  {
    id: 'rates', label: '利率', page: '/rates/',
    apis: ['/api/rates/analysis', '/api/fomc/calendar'],
    num: d => `2s10s ${d[0].yield_curve.spreads['2s10s']}bp`,
    concl: d => d[0].overview.sections[0]?.body || '',
    sub: d => `FOMC ${Number(d[1].target_lower).toFixed(2)}–${Number(d[1].target_upper).toFixed(2)}%`,
    delta: d => fmtDelta('月', d[0].yield_curve.spreads['2s10s_1m_chg'], 'bp', true, 0),
  },
  {
    id: 'inflation', label: '通胀', page: '/inflation/',
    apis: ['/api/inflation/overview'],
    num: d => `核心 CPI ${d[0].cards.core_cpi.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `CPI ${d[0].cards.cpi.value}% · PCE ${d[0].cards.core_pce.value}%`,
    delta: d => fmtDelta('环比', d[0].cards.cpi.chg_1m, 'pp'),
  },
  {
    id: 'labor', label: '就业', page: '/labor/',
    apis: ['/api/labor/overview'],
    num: d => `失业率 ${d[0].cards.unrate.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `非农 3M ${d[0].cards.nfp.avg_3m}K · Sahm ${d[0].cards.unrate.sahm}`,
    delta: d => fmtDelta('3M', d[0].cards.unrate.chg_3m, 'pp'),
  },
  {
    id: 'treasury', label: '美债', page: '/treasury/',
    apis: ['/api/treasury/overview'],
    num: d => `官方占比 ${d[0].cards.official_share.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `海外持仓 $${d[0].cards.hold_total.value}万亿 · Bill ${d[0].cards.bill_share.value}%`,
    delta: d => fmtDelta('年', d[0].cards.hold_total.chg_1y_b, 'B', true, 0, '$'),
  },
  {
    id: 'liquidity', label: '流动性', page: '/liquidity/',
    apis: ['/api/liquidity/overview'],
    num: d => `净流动性 $${(d[0].summary.NET_LIQUIDITY.latest_value / 1e6).toFixed(1)}万亿`,
    concl: d => `总资产 $${(d[0].summary.WALCL.latest_value / 1e6).toFixed(1)}万亿 · 准备金 $${(d[0].summary.WRESBAL.latest_value / 1e6).toFixed(1)}万亿`,
    sub: d => `RRP $${(d[0].summary.RRPONTSYD.latest_value / 1e6).toFixed(1)}万亿 · TGA $${(d[0].summary.WTREGEN.latest_value / 1e6).toFixed(1)}万亿`,
    delta: d => {
      const c = d[0].summary.NET_LIQUIDITY.change_1m;
      return c == null ? null : fmtDelta('1M', Number((c * 100).toFixed(1)), '%');
    },
  },
  {
    id: 'fed', label: '美联储', page: '/fed/',
    apis: ['/api/fed/overview'],
    num: d => `鹰鸽 ${d[0].indicator.label}`,
    concl: d => `立场样本 ${d[0].indicator.sample} 人`,
    delta: d => {
      const pm = d[0].indicator.pre_meeting;
      if (!pm) return null;
      // 与上一次 FOMC 会前 14 天窗口的演讲平均立场对比，转鹰绿 / 转鸽红
      const diff = d[0].indicator.score - pm.score;
      const cls = diff > 0.1 ? 'up' : diff < -0.1 ? 'down' : '';
      return `<span class="macro-side-delta ${cls}" title="${pm.meeting} 会前 ${pm.sample} 篇演讲">会前 ${pm.label}</span>`;
    },
  },
  {
    id: 'credit', label: '信用', page: '/credit/',
    apis: ['/api/credit/overview'],
    num: d => d[0].regime.regime,
    concl: d => `HY-IG 利差 ${d[0].hy_ig.value}bp`,
    delta: d => fmtDelta('1Y分位', d[0].hy_ig.pct_1y, '%', false),
  },
  {
    id: 'vol', label: '波动率', page: '/volatility/',
    apis: ['/api/volatility/analysis'],
    num: d => `VIX ${d[0].vix.value}`,
    concl: d => `${d[0].vix.zone}区间 · ${d[0].vix.percentile_1y}% 分位`,
    sub: d => d[0].signals[0]?.title || '',
    delta: d => fmtDelta('日', d[0].vix.chg_1d_pct, '%'),
  },
];

// ── 变化率格式化（有符号的上/下着色，复用 SPA 的 .up/.down 约定：涨绿跌红）────
function fmtDelta(label, v, unit, signed = true, dec = 1, prefix = '') {
  if (v == null || Number.isNaN(v)) return null;
  const num = dec ? Number(v).toFixed(dec) : String(Math.round(v));
  if (!signed) return `<span class="macro-side-delta">${label} ${prefix}${num}${unit}</span>`;
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : '';
  const sign = v > 0 ? '+' : '';
  return `<span class="macro-side-delta ${cls}">${label} ${sign}${prefix}${num}${unit}</span>`;
}

// ── state ──────────────────────────────────────────────────────────────────
let activeId = null;
const frames = new Map(); // id → iframe（全部预加载，切卡只切换显隐）

function selectTopic(id, updateHash = true) {
  if (activeId === id) return;
  activeId = id;
  document.querySelectorAll('.macro-side-card').forEach(c =>
    c.classList.toggle('active', c.dataset.fid === id));
  frames.forEach((frame, fid) => {
    frame.style.display = fid === id ? '' : 'none';
  });
  if (updateHash) history.replaceState(null, '', '#' + id);
}

// ── render ─────────────────────────────────────────────────────────────────
async function renderSplit(container) {
  const split = document.createElement('div');
  split.className = 'macro-split';

  const side = document.createElement('div');
  side.className = 'macro-side';
  const detail = document.createElement('div');
  detail.className = 'macro-detail';
  split.appendChild(side);
  split.appendChild(detail);
  container.appendChild(split);

  // 全部专题 iframe 一次建好（隐藏待切换），切卡即时且保留各页滚动位置
  FEATURED.forEach(f => {
    const frame = document.createElement('iframe');
    frame.title = f.label;
    frame.src = f.page;
    frame.style.display = 'none';
    frames.set(f.id, frame);
    detail.appendChild(frame);
  });

  const results = await Promise.all(FEATURED.map(async f => {
    try {
      const datas = await Promise.all(f.apis.map(u => fetch(u).then(r => r.json())));
      return { f, datas, err: null };
    } catch (e) {
      return { f, datas: null, err: e };
    }
  }));

  results.forEach(({ f, datas, err }) => {
    const card = document.createElement('div');
    card.className = 'macro-side-card';
    card.dataset.fid = f.id;
    const delta = err || !datas ? null : (f.delta ? f.delta(datas) : null);
    card.innerHTML = `<div class="macro-side-top"><span class="macro-side-title">${f.label}</span>${delta || ''}</div>`
      + (err || !datas
        ? '<div class="loading">加载失败</div>'
        : `
        <div class="macro-side-num">${f.num(datas)}</div>
        <div class="macro-side-concl">${f.concl(datas)}</div>
        ${f.sub ? `<div class="macro-side-sub">${f.sub(datas)}</div>` : ''}`);
    card.addEventListener('click', () => {
      selectTopic(f.id);
      if (split.classList.contains('side-open')) setSideOpen(false);
    });
    side.appendChild(card);
  });

  // 窄屏折叠：顶部 chip 横条 + 浮层展开完整卡片（宽屏不显示，见 app.css 媒体查询）
  const toggleBtn = document.createElement('button');
  toggleBtn.className = 'macro-side-toggle';
  toggleBtn.title = '展开专题卡片';
  toggleBtn.textContent = '▾';
  function setSideOpen(open) {
    split.classList.toggle('side-open', open);
    toggleBtn.textContent = open ? '▴' : '▾';
    toggleBtn.title = open ? '收起专题卡片' : '展开专题卡片';
  }
  toggleBtn.addEventListener('click', () =>
    setSideOpen(!split.classList.contains('side-open')));
  split.appendChild(toggleBtn);
  const backdrop = document.createElement('div');
  backdrop.className = 'macro-side-backdrop';
  backdrop.addEventListener('click', () => setSideOpen(false));
  split.appendChild(backdrop);
  // 窗口变宽离开窄屏时收起浮层，避免残留 side-open 状态
  const mq = window.matchMedia('(max-width: 900px)');
  mq.addEventListener('change', e => { if (!e.matches) setSideOpen(false); });

  // 初始选中：URL hash（刷新保持）> 第一个专题
  const initial = FEATURED.find(f => location.hash === '#' + f.id) || FEATURED[0];
  selectTopic(initial.id, false);

  window.addEventListener('hashchange', () => {
    const f = FEATURED.find(x => location.hash === '#' + x.id);
    if (f && f.id !== activeId) selectTopic(f.id, false);
  });
}

// ── init ───────────────────────────────────────────────────────────────────
export function initMacroView() {
  const container = document.getElementById('macro-chart-card');
  container.innerHTML = '';
  renderSplit(container);
}

// 外部跳转指定专题（如仪表盘净流动性小卡片）：已初始化直接切，未初始化靠 hash 兜底
export function openTopic(id) {
  if (frames.has(id)) selectTopic(id, true);
  else location.hash = id;
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = `${FEATURED.length} 专题`;
  document.getElementById('status-range').textContent = '';
}
