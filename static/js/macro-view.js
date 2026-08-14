// macro-view.js — 宏观引导页：专题速览 tab（当期数字 + 结论 + 跳转专题页；曲线在专题页内查看）

// ── 专题速览 tab ────────────────────────────────────────────────────────────
const FEATURED = [
  {
    id: 'rates', label: '利率', page: '/rates/',
    apis: ['/api/rates/analysis', '/api/fomc/calendar'],
    num: d => `2s10s ${d[0].yield_curve.spreads['2s10s']}bp`,
    concl: d => d[0].overview.sections[0]?.body || '',
    sub: d => `FOMC 目标区间 ${Number(d[1].target_lower).toFixed(2)}% – ${Number(d[1].target_upper).toFixed(2)}%`,
  },
  {
    id: 'inflation', label: '通胀', page: '/inflation/',
    apis: ['/api/inflation/overview'],
    num: d => `核心 CPI ${d[0].cards.core_cpi.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `CPI ${d[0].cards.cpi.value}% · 核心 PCE ${d[0].cards.core_pce.value}%`,
  },
  {
    id: 'labor', label: '就业', page: '/labor/',
    apis: ['/api/labor/overview'],
    num: d => `失业率 ${d[0].cards.unrate.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `非农 3M 均值 ${d[0].cards.nfp.avg_3m}K · Sahm ${d[0].cards.unrate.sahm}`,
  },
  {
    id: 'treasury', label: '美债需求', page: '/treasury/',
    apis: ['/api/treasury/overview'],
    num: d => `官方占比 ${d[0].cards.official_share.value}%`,
    concl: d => d[0].signals[0]?.text || '',
    sub: d => `海外持仓 $${d[0].cards.hold_total.value}万亿 · Bill ${d[0].cards.bill_share.value}%`,
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

// ── init ───────────────────────────────────────────────────────────────────
export function initMacroView() {
  const container = document.getElementById('macro-chart-card');
  container.innerHTML = '';
  renderFeatureTabs(container);
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = '宏观';
  document.getElementById('status-count').textContent = `${FEATURED.length} 专题`;
  document.getElementById('status-range').textContent = '';
}
