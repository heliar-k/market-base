// 期权市场结构 · 负反馈监控子视图渲染引擎（挂载于 assets/equities/options.astro #opt-pane-microstructure）。
// 端点：/api/volatility/microstructure（按需 R.getOpt 加载，失败仅降级本子视图，不影响其他 Tab）。
(function () {
  const SLIDER_SPECS = [
    { key: 'vix', label: 'VIX 恐慌指数', min: 10, max: 60, step: 0.1, unit: '' },
    { key: 'vvix', label: 'VVIX 波动率之波动', min: 70, max: 170, step: 0.5, unit: '' },
    { key: 'skew', label: 'CBOE SKEW 偏度', min: 115, max: 175, step: 0.5, unit: '' },
    { key: 'vix9d', label: 'VIX9D 9日隐波', min: 10, max: 65, step: 0.1, unit: '' },
    { key: 'vix3m', label: 'VIX3M 3月隐波', min: 12, max: 50, step: 0.1, unit: '' },
    { key: 'vixeq', label: 'VIXEQ 个股隐波', min: 25, max: 80, step: 0.1, unit: '' },
    { key: 'cor1m', label: 'COR1M 1月隐含相关性', min: 4, max: 55, step: 0.2, unit: '' },
    { key: 'pc_equity', label: 'CBOE 个股 P/C', min: 0.35, max: 1.15, step: 0.01, unit: '' },
    { key: 'pc_index', label: 'CBOE 指数 P/C', min: 0.7, max: 1.75, step: 0.01, unit: '' },
    { key: 'agg_gex', label: '宽基合计 Net GEX ($B)', min: -10, max: 6, step: 0.1, unit: 'B' },
  ];

  const TONE_MAP = {
    up: { card: 'active-up', badge: 'bull' },
    warn: { card: 'active-warn', badge: 'warn' },
    down: { card: 'active-down', badge: 'bear' },
    brand: { card: 'active-brand', badge: 'flat' },
  };

  const FAIL_IDS = [
    'ms-load-error',
    'ms-stages',
    'ms-kpi-cards',
    'ms-dealer-gauges',
    'ms-disp-gauges',
    'ms-pc-chart',
    'ms-hist-chart',
    'ms-cl-risk',
    'ms-cl-relief',
  ];

  const DEFAULT_SIM_COLLAPSED =
    '当前展示最新交易日真实收盘读数。展开面板后，可一键切换四个典型市场阶段或手动调整 VIX、VVIX、SKEW、VIX9D、VIX3M、VIXEQ、COR1M、Put/Call 与宽基合计 Net GEX，即时联动重算整页评分、传导链、水位条与复盘清单。';

  let payload = null;
  let simInputs = null;
  let simBase = null;
  let activePreset = 'live';
  let simOpen = false;
  let presets = [];
  let currentEval = null;
  let mountPromise = null;
  let notesMounted = false;
  let controlsBound = false;
  let chartsDirty = false;
  const manualOverrides = {};
  const mountedCharts = new Set();

  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const isNum = (v) => {
    if (v === null || v === undefined || typeof v === 'boolean') return false;
    if (typeof v === 'string' && v.trim() === '') return false;
    return Number.isFinite(Number(v));
  };
  const toNum = (v) => (isNum(v) ? Number(v) : null);

  function triOr(...conds) {
    if (conds.some((c) => c === true)) return true;
    if (conds.some((c) => c === null)) return null;
    return false;
  }

  function triAnd(...conds) {
    if (conds.some((c) => c === false)) return false;
    if (conds.some((c) => c === null)) return null;
    return true;
  }

  function weightedAvg(parts) {
    const valid = parts.filter(([v]) => isNum(v));
    if (!valid.length) return null;
    const wSum = valid.reduce((a, [, w]) => a + w, 0);
    return wSum > 0 ? valid.reduce((a, [v, w]) => a + Number(v) * w, 0) / wSum : null;
  }

  function fmtPart(label, val, digits = 2) {
    return val !== null ? `${label} = ${val.toFixed(digits)}` : `${label} 缺失`;
  }

  // 与后端 src/volatility_microstructure.py::compute_microstructure_state 完全同公式、同舍入
  function evaluateLocal(inp, T) {
    const vix = toNum(inp.vix);
    const vvix = toNum(inp.vvix);
    const vix9d = toNum(inp.vix9d);
    const vix3m = toNum(inp.vix3m);
    const skew = toNum(inp.skew);
    const vixeq = toNum(inp.vixeq);
    const cor1m = toNum(inp.cor1m);
    const pcEq = toNum(inp.pc_equity);
    const pcIdx = toNum(inp.pc_index);
    const aggGex = toNum(inp.agg_gex);

    const vr = vix !== null && vix3m !== null && vix3m > 0 ? vix / vix3m : null;
    const sr = vix9d !== null && vix !== null && vix > 0 ? vix9d / vix : null;
    const dr = vixeq !== null && vix !== null && vix > 0 ? vixeq / vix : null;

    const volRaw = weightedAvg([
      [vix !== null ? clamp01((vix - 14.0) / 22.0) * 100 : null, 0.65],
      [vvix !== null ? clamp01((vvix - 85.0) / 50.0) * 100 : null, 0.35],
    ]);
    const termRaw = weightedAvg([
      [vr !== null ? clamp01((vr - 0.85) / 0.25) * 100 : null, 0.45],
      [sr !== null ? clamp01((sr - 0.88) / 0.24) * 100 : null, 0.35],
      [skew !== null ? clamp01((skew - 135.0) / 25.0) * 100 : null, 0.2],
    ]);
    const dealerRaw = aggGex !== null ? clamp01((3.5 - aggGex) / 10.0) * 100 : null;
    const dispRaw = weightedAvg([
      [dr !== null ? clamp01((2.5 - dr) / 0.95) * 100 : null, 0.5],
      [cor1m !== null ? clamp01((cor1m - 10.0) / 26.0) * 100 : null, 0.5],
    ]);
    const flowRaw = weightedAvg([
      [pcEq !== null ? clamp01((pcEq - 0.5) / 0.32) * 100 : null, 0.5],
      [pcIdx !== null ? clamp01((pcIdx - 0.95) / 0.35) * 100 : null, 0.5],
    ]);

    const dimScores = {
      vol: volRaw !== null ? Math.floor(volRaw + 0.5) : null,
      term: termRaw !== null ? Math.floor(termRaw + 0.5) : null,
      dealer: dealerRaw !== null ? Math.floor(dealerRaw + 0.5) : null,
      disp: dispRaw !== null ? Math.floor(dispRaw + 0.5) : null,
      flow: flowRaw !== null ? Math.floor(flowRaw + 0.5) : null,
    };
    const totalRaw = weightedAvg([
      [dimScores.vol, 0.25],
      [dimScores.term, 0.15],
      [dimScores.dealer, 0.3],
      [dimScores.disp, 0.15],
      [dimScores.flow, 0.15],
    ]);
    const score = totalRaw !== null ? Math.floor(totalRaw + 0.5) : null;

    const invMain = vr !== null ? vr > T.term_invert : null;
    const invShort = sr !== null ? sr > T.term_invert : null;
    const invertedTri = triOr(invMain, invShort);
    const inverted = invertedTri === true;

    let stageId = null;
    if (score !== null) {
      if ((inverted && aggGex !== null && aggGex < 0 && vix !== null && vix >= 22.0) || score >= 65) {
        stageId = 3;
      } else if (
        inverted ||
        (skew !== null && skew >= T.skew_warn) ||
        (vvix !== null && vvix >= T.vvix_warn) ||
        (dr !== null && dr < 2.05) ||
        (aggGex !== null && aggGex < 0) ||
        (vix !== null && vix >= 20.0) ||
        score >= 35
      ) {
        stageId = 2;
      } else if (
        invertedTri === false &&
        vr !== null &&
        vr < 0.96 &&
        vix !== null &&
        vix >= 17.0 &&
        vvix !== null &&
        vvix < T.vvix_relief &&
        (aggGex === null || aggGex >= 0)
      ) {
        stageId = 4;
      } else {
        stageId = 1;
      }
    }

    const chainSt = (lit, warn) => (lit === null && warn === null ? 'missing' : lit ? 'lit' : warn ? 'warn' : 'normal');
    const chain = [
      {
        step: '1. 相关性抬升',
        val: cor1m !== null ? `COR1M ${cor1m.toFixed(1)}` : 'COR1M 缺失',
        state: chainSt(cor1m !== null ? cor1m >= T.cor1m_risk : null, cor1m !== null ? cor1m >= T.cor1m_warn : null),
      },
      {
        step: '2. 离散度压缩',
        val: dr !== null ? `VIXEQ/VIX ${dr.toFixed(2)}` : 'VIXEQ/VIX 缺失',
        state: chainSt(dr !== null ? dr < T.disp_risk : null, dr !== null ? dr < T.disp_relief : null),
      },
      {
        step: '3. 负 Gamma',
        val: aggGex !== null ? `宽基 ${aggGex >= 0 ? '+' : ''}$${aggGex.toFixed(2)}B` : '宽基 GEX 缺失',
        state: chainSt(aggGex !== null ? aggGex < -2.0 : null, aggGex !== null ? aggGex < 0 : null),
      },
      {
        step: '4. 期限倒挂',
        val: vr !== null ? `VIX/3M ${vr.toFixed(2)}` : 'VIX/3M 缺失',
        state: chainSt(vr !== null ? vr > T.term_invert : null, vr !== null ? vr > 0.96 : null),
      },
      {
        step: '5. Vanna 加速',
        val: vvix !== null ? `VVIX ${vvix.toFixed(1)}` : 'VVIX 缺失',
        state: chainSt(
          vvix !== null ? vvix >= T.vvix_stress && (aggGex === null || aggGex < 0) : null,
          vvix !== null ? vvix >= T.vvix_warn : null
        ),
      },
    ];

    const risk = [
      {
        id: 'r1',
        title: '相关性突破与离散度压缩',
        cond: `VIXEQ/VIX < ${T.disp_risk.toFixed(2)} 或 COR1M ≥ ${T.cor1m_risk.toFixed(0)}，离散头寸面临平仓压力`,
        read: `${fmtPart('VIXEQ/VIX', dr, 2)} · ${fmtPart('COR1M', cor1m, 2)}`,
        met: triOr(dr !== null ? dr < T.disp_risk : null, cor1m !== null ? cor1m >= T.cor1m_risk : null),
      },
      {
        id: 'r2',
        title: '波动率期限结构倒挂',
        cond: `VIX ≥ ${T.vix_risk.toFixed(0)} 且 VIX/VIX3M > 1.00 或 VIX9D/VIX > 1.00（贴水确认）`,
        read: `${fmtPart('VIX', vix, 2)} · ${fmtPart('VIX/VIX3M', vr, 3)} · ${fmtPart('VIX9D/VIX', sr, 3)}`,
        met: triAnd(vix !== null ? vix >= T.vix_risk : null, invertedTri),
      },
      {
        id: 'r3',
        title: '宽基做市商翻入净负 Gamma',
        cond: 'SPY + QQQ + IWM 汇总 Net GEX < 0，对冲流由缓冲转为顺势放大',
        read: aggGex !== null ? `当前汇总 = ${aggGex >= 0 ? '+' : ''}$${aggGex.toFixed(2)}B` : '宽基期权快照缺失',
        met: aggGex === null ? null : aggGex < 0,
      },
      {
        id: 'r4',
        title: '期权流量防卫溢价扩散',
        cond: `CBOE 指数 P/C ≥ ${T.pc_index_risk.toFixed(2)} 或个股 P/C ≥ ${T.pc_equity_risk.toFixed(2)}，保护买盘放量`,
        read: `${fmtPart('Index P/C', pcIdx, 2)} · ${fmtPart('Equity P/C', pcEq, 2)}`,
        met: triOr(pcIdx !== null ? pcIdx >= T.pc_index_risk : null, pcEq !== null ? pcEq >= T.pc_equity_risk : null),
      },
    ];

    const relief = [
      {
        id: 'f1',
        title: '期限结构恢复健康升水',
        cond: 'VIX9D/VIX < 1.00 且 VIX/VIX3M < 1.00，短端恐慌溢价消退',
        read: `${fmtPart('VIX9D/VIX', sr, 3)} · ${fmtPart('VIX/VIX3M', vr, 3)}`,
        met: triAnd(sr !== null ? sr < T.term_invert : null, vr !== null ? vr < T.term_invert : null),
      },
      {
        id: 'f2',
        title: 'VVIX 先行见顶回落',
        cond: `VVIX < ${T.vvix_relief.toFixed(0)} 且 VIX < ${T.vix_relief.toFixed(0)}，波动率期权买盘降温`,
        read: `${fmtPart('VVIX', vvix, 1)} · ${fmtPart('VIX', vix, 2)}`,
        met: triAnd(vvix !== null ? vvix < T.vvix_relief : null, vix !== null ? vix < T.vix_relief : null),
      },
      {
        id: 'f3',
        title: '个股与指数波动比回升',
        cond: `VIXEQ/VIX ≥ ${T.disp_relief.toFixed(2)} 且 COR1M < ${T.cor1m_relief.toFixed(0)}，相关性回落至常态区间`,
        read: `${fmtPart('VIXEQ/VIX', dr, 2)} · ${fmtPart('COR1M', cor1m, 2)}`,
        met: triAnd(dr !== null ? dr >= T.disp_relief : null, cor1m !== null ? cor1m < T.cor1m_relief : null),
      },
      {
        id: 'f4',
        title: '宽基净 GEX 企稳翻正',
        cond: 'SPY + QQQ + IWM 汇总 Net GEX ≥ 0，做市商恢复低买高卖缓冲',
        read: aggGex !== null ? `当前汇总 = ${aggGex >= 0 ? '+' : ''}$${aggGex.toFixed(2)}B` : '宽基期权快照缺失',
        met: aggGex === null ? null : aggGex >= 0,
      },
    ];

    const stageObj = (payload.stages || []).find((s) => s.id === stageId) || null;
    return {
      score,
      stage_id: stageId,
      stage_name: stageObj ? stageObj.name : null,
      stage_desc: stageObj ? stageObj.desc : '核心数据源缺失，无法完成阶段判定。',
      dims: [
        { key: 'vol', name: '波动率挤兑', weight: 0.25, score: dimScores.vol },
        { key: 'term', name: '期限与偏度', weight: 0.15, score: dimScores.term },
        { key: 'dealer', name: '做市商敞口', weight: 0.3, score: dimScores.dealer },
        { key: 'disp', name: '离散相关性', weight: 0.15, score: dimScores.disp },
        { key: 'flow', name: '期权流量', weight: 0.15, score: dimScores.flow },
      ],
      chain,
      inverted,
      vr,
      sr,
      dr,
      checklists: { risk, relief },
    };
  }

  // 图形档（进度条填充）与文字档（数字/标签）严格分开，保证亮暗双主题 AA 对比度
  const scoreGraphic = (v) =>
    !isNum(v)
      ? 'var(--color-neutral)'
      : v >= 70
        ? 'var(--color-down)'
        : v >= 45
          ? 'var(--color-warn)'
          : v >= 25
            ? 'var(--color-brand)'
            : 'var(--color-up)';
  const scoreText = (v) =>
    !isNum(v)
      ? 'var(--text-dim)'
      : v >= 70
        ? 'var(--down)'
        : v >= 45
          ? 'var(--warning)'
          : v >= 25
            ? 'var(--accent-ink)'
            : 'var(--up)';

  function renderGaugeZero(name, sub, value, maxAbs, leftLbl, rightLbl, footMid, pinValue = null) {
    if (!isNum(value)) {
      return `
        <div class="ms-gauge-row">
          <div class="ms-gauge-top">
            <div><span class="ms-gauge-name">${R.esc(name)}</span><span class="ms-gauge-sub">${R.esc(sub)}</span></div>
            <span class="ms-gauge-val" style="color:var(--text-dim)">—</span>
          </div>
          <div class="ms-zero-track"><div class="ms-zero-axis"></div></div>
          <div class="ms-gauge-foot"><span>${R.esc(leftLbl)}</span><span>缺数据</span><span>${R.esc(rightLbl)}</span></div>
        </div>`;
    }
    const v = Number(value);
    const pct = Math.min(50, (Math.abs(v) / maxAbs) * 50);
    const left = v < 0 ? 50 - pct : 50;
    const fill = v < 0 ? 'var(--color-down)' : 'var(--color-up)';
    const valColor = v < 0 ? 'var(--down)' : 'var(--up)';
    const sign = v > 0 ? '+' : '';
    let pinHtml = '';
    if (isNum(pinValue)) {
      const pinPct = clamp01((Number(pinValue) + maxAbs) / (2 * maxAbs)) * 100;
      pinHtml = `<div class="ms-threshold-pin" style="left:${pinPct.toFixed(1)}%" title="历史底线 $${Number(pinValue).toFixed(2)}B"></div>`;
    }
    return `
      <div class="ms-gauge-row">
        <div class="ms-gauge-top">
          <div><span class="ms-gauge-name">${R.esc(name)}</span><span class="ms-gauge-sub">${R.esc(sub)}</span></div>
          <span class="ms-gauge-val" style="color:${valColor}">${sign}$${v.toFixed(2)}B</span>
        </div>
        <div class="ms-zero-track">
          <div class="ms-zero-axis"></div>
          <div class="ms-zero-fill" style="left:${left.toFixed(1)}%;width:${pct.toFixed(1)}%;background:${fill}"></div>
          ${pinHtml}
        </div>
        <div class="ms-gauge-foot">
          <span>${R.esc(leftLbl)}</span>
          <span>${R.esc(footMid)}</span>
          <span>${R.esc(rightLbl)}</span>
        </div>
      </div>`;
  }

  function renderGaugeLinear(name, sub, value, min, max, threshold, invert, unit, footMid) {
    if (!isNum(value)) {
      return `
        <div class="ms-gauge-row">
          <div class="ms-gauge-top">
            <div><span class="ms-gauge-name">${R.esc(name)}</span><span class="ms-gauge-sub">${R.esc(sub)}</span></div>
            <span class="ms-gauge-val" style="color:var(--text-dim)">—</span>
          </div>
          <div class="ms-linear-track"></div>
          <div class="ms-gauge-foot"><span>${min}</span><span>缺数据</span><span>${max}</span></div>
        </div>`;
    }
    const v = Number(value);
    const pct = clamp01((v - min) / (max - min)) * 100;
    const pinPct = clamp01((threshold - min) / (max - min)) * 100;
    const bad = invert ? v < threshold : v >= threshold;
    const fill = bad ? 'var(--color-down)' : 'var(--color-brand)';
    const valColor = bad ? 'var(--down)' : 'var(--text)';
    return `
      <div class="ms-gauge-row">
        <div class="ms-gauge-top">
          <div><span class="ms-gauge-name">${R.esc(name)}</span><span class="ms-gauge-sub">${R.esc(sub)}</span></div>
          <span class="ms-gauge-val" style="color:${valColor}">${v.toFixed(2)}${R.esc(unit)}</span>
        </div>
        <div class="ms-linear-track">
          <div class="ms-linear-fill" style="width:${pct.toFixed(1)}%;background:${fill}"></div>
          <div class="ms-threshold-pin" style="left:${pinPct.toFixed(1)}%" title="警戒线 ${threshold}"></div>
        </div>
        <div class="ms-gauge-foot">
          <span>${min}</span>
          <span>${R.esc(footMid)}</span>
          <span>${max}</span>
        </div>
      </div>`;
  }

  function renderChecklist(targetId, items, kind) {
    const el = document.getElementById(targetId);
    if (!el) return 0;
    let activeCount = 0;
    el.innerHTML = items
      .map((it) => {
        const hasOverride = it.id in manualOverrides;
        const checked = hasOverride ? manualOverrides[it.id] : Boolean(it.met);
        if (checked) activeCount += 1;
        const hitCls = checked ? (kind === 'risk' ? 'hit-risk' : 'hit-relief') : '';

        let badgeCls = 'flat';
        let badgeTxt = kind === 'risk' ? '未触发' : '待确认';
        if (hasOverride) {
          if (checked) {
            badgeCls = kind === 'risk' ? 'bear' : 'bull';
            badgeTxt = '人工确认';
          } else {
            badgeCls = it.met === true ? 'warn' : 'flat';
            badgeTxt = it.met === true ? '人工排除（原已触发）' : it.met === null ? '人工排除（原缺失）' : '人工排除';
          }
        } else if (it.met === null) {
          badgeCls = 'flat';
          badgeTxt = '缺数据';
        } else if (it.met === true) {
          badgeCls = kind === 'risk' ? 'bear' : 'bull';
          badgeTxt = kind === 'risk' ? '指标已触发' : '条件已满足';
        }

        return `
          <label class="ms-cl-item ${hitCls}" data-cid="${R.esc(it.id)}">
            <input type="checkbox" ${checked ? 'checked' : ''} data-cid="${R.esc(it.id)}" />
            <div class="ms-cl-body">
              <div class="ms-cl-title-row">
                <span class="ms-cl-title">${R.esc(it.title)}</span>
                <span class="re-badge ${badgeCls}">${badgeTxt}</span>
              </div>
              <div class="ms-cl-cond">${R.esc(it.cond)}</div>
              <div class="ms-cl-read">${R.esc(it.read)}</div>
            </div>
          </label>`;
      })
      .join('');

    el.querySelectorAll('input[type="checkbox"]').forEach((box) => {
      box.addEventListener('change', (e) => {
        manualOverrides[e.target.dataset.cid] = e.target.checked;
        renderAll();
      });
    });
    return activeCount;
  }

  function isPaneVisible() {
    const pane = document.getElementById('opt-pane-microstructure');
    return Boolean(pane && pane.offsetParent !== null && pane.clientWidth > 0);
  }

  // ECharts 零泄漏刷新：仅在容器可见且首次时走 R.mkChart 挂载监听，后续推演原地 setOption
  function upsertChart(id, buildFn) {
    const dom = document.getElementById(id);
    if (!dom) return;
    if (!isPaneVisible() || dom.clientWidth === 0) {
      chartsDirty = true;
      return;
    }
    const inst = R.getChart(id);
    if (inst) {
      const host = dom.querySelector(':scope > .re-chart-pad') || dom;
      const opt = R._clampStroke(
        R._clampGrid(buildFn(R.colors(), host.clientWidth), host.clientWidth),
        host.clientWidth
      );
      opt.aria = Object.assign({}, opt.aria, { description: R._ariaText(opt, dom) });
      inst.setOption(opt, { notMerge: true });
      R._fitLater(inst, host, 0);
      return;
    }
    if (!mountedCharts.has(id)) {
      const created = R.mkChart(id, buildFn);
      if (created || (R._pendingCharts && R._pendingCharts.has(id))) {
        mountedCharts.add(id);
      }
    }
  }

  function buildPcOption(colors) {
    const isSim = activePreset !== 'live';
    const base = simBase || payload.live_inputs || {};
    const pcKeyMap = {
      SPX_PC: 'pc_spx',
      INDEX_PC: 'pc_index',
      VIX_PC: 'pc_vix',
      ETP_PC: 'pc_etp',
      TOTAL_PC: 'pc_total',
      EQUITY_PC: 'pc_equity',
    };
    const dIdx = isNum(simInputs.pc_index) && isNum(base.pc_index) ? simInputs.pc_index - base.pc_index : 0;
    const dEq = isNum(simInputs.pc_equity) && isNum(base.pc_equity) ? simInputs.pc_equity - base.pc_equity : 0;

    const pcItems = (payload.put_call?.items || []).map((it) => {
      const k = pcKeyMap[it.key];
      let v = isSim && k ? simInputs[k] : it.value;
      if (activePreset === 'custom') {
        if (it.key === 'SPX_PC' && isNum(base.pc_spx)) v = Math.max(0.4, base.pc_spx + dIdx);
        if (it.key === 'TOTAL_PC' && isNum(base.pc_total)) v = Math.max(0.4, base.pc_total + 0.5 * (dIdx + dEq));
      }
      return { name: it.label, v: toNum(v), warn: it.warn, pctile: it.pctile_1y };
    });

    const pcVals = pcItems.map((x) => x.v).filter(isNum);
    const maxAxis = Math.max(1.6, Math.ceil((Math.max(1.2, ...pcVals) + 0.25) * 5) / 5);

    return reSyncLegend({
      grid: { left: 8, right: 36, top: 22, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = params[0];
          if (!p) return '';
          const row = pcItems[p.dataIndex];
          const pctStr = !isSim && isNum(row?.pctile) ? ` · 1Y 分位 ${row.pctile}%` : '';
          return `${R.esc(p.name)}：<b>${isNum(p.value) ? Number(p.value).toFixed(2) : '—'}</b>${pctStr}`;
        },
      },
      xAxis: { type: 'value', min: 0, max: maxAxis },
      yAxis: { type: 'category', data: pcItems.map((x) => x.name), inverse: true },
      series: [
        {
          name: 'Put/Call Ratio',
          type: 'bar',
          barMaxWidth: 16,
          data: pcItems.map((x) => ({
            value: isNum(x.v) ? Number(Number(x.v).toFixed(2)) : null,
            itemStyle: {
              color: !isNum(x.v)
                ? colors.gray
                : x.v >= x.warn
                  ? colors.red
                  : x.v >= x.warn * 0.9
                    ? colors.orange
                    : colors.blue,
              borderRadius: [0, 3, 3, 0],
            },
          })),
          label: {
            show: true,
            position: 'right',
            formatter: (p) => (isNum(p.value) ? Number(p.value).toFixed(2) : '—'),
            color: colors.muted,
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 11,
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: colors.orange, type: 'dashed', width: 1 },
            label: { position: 'start', distance: 4, formatter: '1.00', color: colors.orangeText, fontSize: 10 },
            data: [{ xAxis: 1.0 }],
          },
        },
      ],
    });
  }

  function buildHistoryOption(colors) {
    const h = payload.history || {};
    const disp = h.vixeq_vix || [];
    const cor1m = h.cor1m || [];
    const dispThr = payload.thresholds.disp_risk;
    const LINE_MAIN = 2;
    const LINE_SUB = 1.5;

    return R.lineOption(
      {
        legend: { top: 0, left: 'center', data: ['VIXEQ / VIX 离散比', 'COR1M 隐含相关性'] },
        grid: { top: 44, bottom: 26 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'time', boundaryGap: false },
        yAxis: [
          { type: 'value', scale: true, name: 'VIXEQ/VIX' },
          { type: 'value', scale: true, name: 'COR1M', splitLine: { show: false } },
        ],
        series: [
          {
            name: 'VIXEQ / VIX 离散比',
            type: 'line',
            yAxisIndex: 0,
            data: disp,
            symbol: 'none',
            lineStyle: { color: colors.blue, width: LINE_MAIN },
            itemStyle: { color: colors.blue },
            markLine: {
              silent: true,
              symbol: 'none',
              lineStyle: { color: colors.red, type: 'dashed', width: 1 },
              label: {
                formatter: dispThr.toFixed(2),
                color: colors.redText,
                fontSize: 10,
                position: 'insideEndTop',
              },
              data: [{ yAxis: dispThr }],
            },
          },
          {
            name: 'COR1M 隐含相关性',
            type: 'line',
            yAxisIndex: 1,
            data: cor1m,
            symbol: 'none',
            lineStyle: { color: colors.orange, width: LINE_SUB },
            itemStyle: { color: colors.orange },
          },
        ],
      },
      colors
    );
  }

  function renderPcChart() {
    const T = payload.thresholds;
    const pcBadge = document.getElementById('ms-pc-badge');
    const pcIdx = toNum(simInputs.pc_index);
    const pcEq = toNum(simInputs.pc_equity);
    const pcHot = (pcIdx !== null && pcIdx >= T.pc_index_risk) || (pcEq !== null && pcEq >= T.pc_equity_risk);
    if (pcBadge) {
      pcBadge.innerHTML =
        pcIdx === null && pcEq === null
          ? '<span class="re-badge flat">缺数据</span>'
          : `<span class="re-badge ${pcHot ? 'warn' : 'flat'}">${pcHot ? '防卫溢价偏高' : '流量中性'}</span>`;
    }
    upsertChart('ms-pc-chart', buildPcOption);
  }

  function pctTag(metricObj, fallbackText, isSim) {
    if (isSim) return fallbackText;
    return isNum(metricObj?.pctile_1y) ? `${fallbackText} · 1Y分位 ${metricObj.pctile_1y}%` : fallbackText;
  }

  function renderAll(skipNumberInputKey = null) {
    if (!payload) return;
    const isSim = activePreset !== 'live';
    const T = payload.thresholds;
    const ev = isSim
      ? evaluateLocal(simInputs, T)
      : {
          score: payload.score,
          stage_id: payload.stage_id,
          stage_name: payload.stage_name,
          stage_desc: payload.stage_desc,
          dims: payload.dims,
          chain: payload.chain,
          inverted: Boolean(payload.inverted),
          vr: payload.metrics?.vix_vix3m?.value ?? null,
          sr: payload.metrics?.vix9d_vix?.value ?? null,
          dr: payload.metrics?.vixeq_vix?.value ?? null,
          checklists: payload.checklists,
        };
    currentEval = ev;

    const stages = payload.stages || [];
    const curStage = stages.find((x) => x.id === ev.stage_id) || null;
    const curTone = curStage ? TONE_MAP[curStage.tone] || TONE_MAP.brand : TONE_MAP.brand;

    const banner = document.getElementById('ms-sim-banner');
    if (banner) banner.classList.toggle('on', isSim);
    if (isSim) {
      const p = presets.find((x) => x.key === activePreset);
      const bannerTxt = document.getElementById('ms-sim-banner-text');
      if (bannerTxt) {
        bannerTxt.textContent = p
          ? `当前处于情景推演模式（${p.label}），所有评分、图表与确认清单均按推演参数即时重算。`
          : '当前处于自定义参数推演模式，所有评分、图表与确认清单均按推演参数即时重算。';
      }
    }

    const scoreEl = document.getElementById('ms-score-val');
    if (scoreEl) {
      scoreEl.textContent = isNum(ev.score) ? String(ev.score) : '—';
      scoreEl.style.color = scoreText(ev.score);
    }

    const regBadge = document.getElementById('ms-regime-badge');
    if (regBadge) {
      regBadge.className = `re-badge ${curStage ? curTone.badge : 'flat'}`;
      regBadge.textContent = curStage ? `阶段 ${curStage.id} · ${curStage.name}` : '缺数据';
    }
    const summaryEl = document.getElementById('ms-score-summary');
    if (summaryEl) summaryEl.textContent = ev.stage_desc || '';

    const dimsEl = document.getElementById('ms-dims-list');
    if (dimsEl) {
      dimsEl.innerHTML = (ev.dims || [])
        .map((d) => {
          const hasVal = isNum(d.score);
          const wPct = Math.round((d.weight || 0) * 100);
          return `
            <div class="ms-dim-item">
              <div class="ms-dim-top">
                <span class="ms-dim-name">${R.esc(d.name)} <span class="ms-dim-w">${wPct}%</span></span>
                <span class="ms-dim-score" style="color:${scoreText(d.score)}">${hasVal ? d.score : '—'}</span>
              </div>
              <div class="ms-bar-track">
                <div class="ms-bar-fill" style="width:${hasVal ? d.score : 0}%;background:${scoreGraphic(d.score)}"></div>
              </div>
            </div>`;
        })
        .join('');
    }

    const chainEl = document.getElementById('ms-chain');
    if (chainEl) {
      chainEl.innerHTML = (ev.chain || [])
        .map((c) => {
          const cls = c.state === 'lit' ? 'lit' : c.state === 'warn' ? 'warn' : '';
          const tag =
            c.state === 'lit'
              ? '<span class="re-badge bear">激活</span>'
              : c.state === 'warn'
                ? '<span class="re-badge warn">预警</span>'
                : c.state === 'missing'
                  ? '<span class="re-badge flat">缺失</span>'
                  : '<span class="re-badge flat">常态</span>';
          return `
            <div class="ms-chain-step ${cls}">
              <div class="ms-chain-k"><span>${R.esc(c.step)}</span>${tag}</div>
              <div class="ms-chain-v">${R.esc(c.val)}</div>
            </div>`;
        })
        .join('');
    }

    const stagesEl = document.getElementById('ms-stages');
    if (stagesEl) {
      stagesEl.innerHTML = stages
        .map((st) => {
          const isCur = st.id === ev.stage_id;
          const tone = TONE_MAP[st.tone] || TONE_MAP.brand;
          return `
            <div class="ms-stage-card ${isCur ? tone.card : ''}">
              <div class="ms-stage-head">
                <span class="ms-stage-num">${R.esc(st.code)}</span>
                ${isCur ? `<span class="re-badge ${tone.badge}">当前所处</span>` : ''}
              </div>
              <div class="ms-stage-name">${R.esc(st.name)}</div>
              <div class="ms-stage-desc">${R.esc(st.desc)}</div>
              <div class="ms-stage-rule">${R.esc(st.rule)}</div>
            </div>`;
        })
        .join('');
    }

    const m = payload.metrics || {};
    const vixV = toNum(simInputs.vix);
    const vvixV = toNum(simInputs.vvix);
    const cor1mV = toNum(simInputs.cor1m);
    const dspxV = toNum(simInputs.dspx);
    const pcIdxV = toNum(simInputs.pc_index);
    const pcEqV = toNum(simInputs.pc_equity);
    const pcTotV = toNum(simInputs.pc_total);
    const aggGexV = toNum(simInputs.agg_gex);

    const kpiHost = document.getElementById('ms-kpi-cards');
    if (kpiHost) {
      kpiHost.innerHTML = '';
      kpiHost.appendChild(
        R.cards([
          {
            label: 'VIX 恐慌指数',
            value: R.num(vixV, 2),
            sub: pctTag(m.vix, vixV !== null && vixV >= T.vix_risk ? `高于 ${T.vix_risk} 警戒` : '常态波动', isSim),
            vcolor: vixV !== null && vixV >= T.vix_risk ? 'var(--down)' : 'var(--text)',
          },
          {
            label: 'VVIX 波动率之波动',
            value: R.num(vvixV, 1),
            sub: pctTag(
              m.vvix,
              vvixV !== null && vvixV >= T.vvix_warn ? `高于 ${T.vvix_warn} 预警` : '预期平稳',
              isSim
            ),
            vcolor:
              vvixV !== null && vvixV >= T.vvix_stress
                ? 'var(--down)'
                : vvixV !== null && vvixV >= T.vvix_warn
                  ? 'var(--warning)'
                  : 'var(--text)',
          },
          {
            label: 'VIX / VIX3M 期限比',
            value: ev.vr !== null ? ev.vr.toFixed(3) : '—',
            sub: pctTag(m.vix_vix3m, ev.vr !== null && ev.vr > T.term_invert ? '主期限贴水' : '主期限升水', isSim),
            vcolor:
              ev.vr !== null && ev.vr > T.term_invert
                ? 'var(--down)'
                : ev.vr !== null
                  ? 'var(--up)'
                  : 'var(--text-dim)',
          },
          {
            label: 'VIX9D / VIX 短端比',
            value: ev.sr !== null ? ev.sr.toFixed(3) : '—',
            sub: pctTag(m.vix9d_vix, ev.sr !== null && ev.sr > T.term_invert ? '短端倒挂' : '短端升水', isSim),
            vcolor:
              ev.sr !== null && ev.sr > T.term_invert
                ? 'var(--down)'
                : ev.sr !== null
                  ? 'var(--up)'
                  : 'var(--text-dim)',
          },
        ])
      );
      kpiHost.appendChild(
        R.cards([
          {
            label: 'VIXEQ / VIX 离散比',
            value: ev.dr !== null ? ev.dr.toFixed(2) : '—',
            sub: pctTag(
              m.vixeq_vix,
              ev.dr !== null && ev.dr < T.disp_risk ? `跌破 ${T.disp_risk.toFixed(2)} 红线` : '离散充裕',
              isSim
            ),
            vcolor:
              ev.dr !== null && ev.dr < T.disp_risk ? 'var(--down)' : ev.dr !== null ? 'var(--up)' : 'var(--text-dim)',
          },
          {
            label: 'COR1M / DSPX',
            value: `${R.num(cor1mV, 1)} / ${R.num(dspxV, 1)}`,
            sub: pctTag(m.cor1m, cor1mV !== null && cor1mV >= T.cor1m_risk ? '相关性急升' : '相关性受控', isSim),
            vcolor: cor1mV !== null && cor1mV >= T.cor1m_risk ? 'var(--down)' : 'var(--text)',
          },
          {
            label: 'CBOE 指数 / 个股 P/C',
            value: `${R.num(pcIdxV, 2)} / ${R.num(pcEqV, 2)}`,
            sub: pcTotV !== null ? `全市场 Total P/C ${pcTotV.toFixed(2)}` : '全市场 P/C 缺失',
            vcolor:
              (pcIdxV !== null && pcIdxV >= T.pc_index_risk) || (pcEqV !== null && pcEqV >= T.pc_equity_risk)
                ? 'var(--warning)'
                : 'var(--text)',
          },
          {
            label: '宽基汇总 Net GEX',
            value: aggGexV !== null ? `${aggGexV >= 0 ? '+' : ''}$${aggGexV.toFixed(2)}B` : '—',
            sub:
              !isSim && isNum(payload.dealer?.pctile)
                ? `${aggGexV < 0 ? '净负 Gamma' : '净正 Gamma'} · 历史分位 ${payload.dealer.pctile}%`
                : aggGexV !== null
                  ? aggGexV < 0
                    ? '净负 Gamma 放大波动'
                    : '正 Gamma 缓冲'
                  : '宽基快照缺失',
            vcolor: aggGexV === null ? 'var(--text-dim)' : aggGexV < 0 ? 'var(--down)' : 'var(--up)',
          },
        ])
      );
    }

    const dlr = payload.dealer || {};
    const maxAbs = Math.max(dlr.max_abs || 7, aggGexV !== null ? Math.ceil(Math.abs(aggGexV) * 1.15) : 7);
    const bufText =
      aggGexV !== null && isNum(dlr.hist_min)
        ? `历史底线 $${dlr.hist_min.toFixed(2)}B（缓冲 ${(aggGexV - dlr.hist_min).toFixed(2)}B）`
        : '0 轴翻转';

    const liveAgg = toNum(payload.live_inputs?.agg_gex);
    const symShares = { SPY: 0.5, QQQ: 0.2, IWM: 0.3 };
    const baseSyms =
      (dlr.symbols || []).length > 0 ? dlr.symbols : ['SPY', 'QQQ', 'IWM'].map((symbol) => ({ symbol, net_gex: null }));

    const symRows = baseSyms
      .map((s) => {
        const share = symShares[s.symbol] || 0.33;
        let gex = toNum(s.net_gex);
        if (isSim && aggGexV !== null) {
          gex = gex !== null && liveAgg !== null ? gex + (aggGexV - liveAgg) * share : aggGexV * share;
        }
        let subText = '';
        if (isSim) {
          subText = `推演按权重分摊（${Math.round(share * 100)}%）`;
        } else {
          const subParts = [];
          if (isNum(s.spot)) subParts.push(`现价 $${Number(s.spot).toFixed(2)}`);
          if (isNum(s.put_wall) && isNum(s.call_wall)) subParts.push(`Put墙 $${s.put_wall} / Call墙 $${s.call_wall}`);
          subText = subParts.join(' · ');
        }
        const baseScale =
          s.symbol === 'SPY' ? Math.max(5, Math.ceil(maxAbs * 0.65)) : Math.max(4, Math.ceil(maxAbs * 0.5));
        const scale = gex !== null ? Math.max(baseScale, Math.ceil(Math.abs(gex) * 1.15)) : baseScale;
        return renderGaugeZero(`${s.symbol} Net GEX`, subText, gex, scale, `-$${scale}B`, `+$${scale}B`, '0');
      })
      .join('');

    const dealerHost = document.getElementById('ms-dealer-gauges');
    if (dealerHost) {
      dealerHost.innerHTML =
        renderGaugeZero(
          '宽基合计 Net GEX',
          `SPY + QQQ + IWM${dlr.hist_count ? ` · 近 ${dlr.hist_count} 日快照` : ''}`,
          aggGexV,
          maxAbs,
          `-$${maxAbs}B`,
          `+$${maxAbs}B`,
          bufText,
          dlr.hist_min
        ) + symRows;
    }

    const dispHost = document.getElementById('ms-disp-gauges');
    if (dispHost) {
      dispHost.innerHTML =
        renderGaugeLinear(
          'VIXEQ / VIX 离散比',
          '个股隐波 ÷ 指数隐波（越低越危险）',
          ev.dr,
          1.2,
          3.2,
          T.disp_risk,
          true,
          'x',
          `压缩警戒 ${T.disp_risk.toFixed(2)}x`
        ) +
        renderGaugeLinear(
          'COR1M 1月隐含相关性',
          'CBOE 标普 500 1月隐含相关性',
          cor1mV,
          4,
          48,
          T.cor1m_risk,
          false,
          '',
          `飙升警戒 ${T.cor1m_risk.toFixed(0)}`
        ) +
        renderGaugeLinear(
          'COR3M 3月隐含相关性',
          'CBOE 标普 500 3月隐含相关性',
          toNum(simInputs.cor3m),
          6,
          42,
          T.cor3m_warn,
          false,
          '',
          `趋势警戒 ${T.cor3m_warn.toFixed(0)}`
        ) +
        renderGaugeLinear(
          'DSPX 标普离散度指数',
          'CBOE 30日隐含离散度',
          dspxV,
          20,
          52,
          T.dspx_warn,
          false,
          '',
          `高位参考 ${T.dspx_warn.toFixed(0)}`
        );
    }

    const riskHits = renderChecklist('ms-cl-risk', ev.checklists.risk, 'risk');
    const reliefHits = renderChecklist('ms-cl-relief', ev.checklists.relief, 'relief');
    const riskBadge = document.getElementById('ms-risk-count-badge');
    if (riskBadge) {
      riskBadge.innerHTML = `<span class="re-badge ${riskHits >= 2 ? 'bear' : 'flat'}">已触发 ${riskHits} / 4</span>`;
    }
    const reliefBadge = document.getElementById('ms-relief-count-badge');
    if (reliefBadge) {
      reliefBadge.innerHTML = `<span class="re-badge ${reliefHits >= 2 ? 'bull' : 'flat'}">已满足 ${reliefHits} / 4</span>`;
    }
    const cntSummary = document.getElementById('ms-counts-summary');
    if (cntSummary) cntSummary.textContent = `风险升级 ${riskHits}/4 · 止跌修复 ${reliefHits}/4`;

    renderPcChart();
    syncSliders(skipNumberInputKey);
    syncPresetButtons();
  }

  function syncSliders(skipNumberInputKey = null) {
    SLIDER_SPECS.forEach((sp) => {
      const r = document.getElementById(`ms-r-${sp.key}`);
      const n = document.getElementById(`ms-n-${sp.key}`);
      const v = toNum(simInputs[sp.key]);
      if (v === null) {
        if (n && sp.key !== skipNumberInputKey) {
          n.value = '';
          n.placeholder = '缺失';
        }
        if (r) r.value = String((sp.min + sp.max) / 2);
        return;
      }
      const clampedRange = Math.max(sp.min, Math.min(sp.max, v));
      if (r && Number(r.value) !== clampedRange) r.value = String(clampedRange);
      if (n && sp.key !== skipNumberInputKey && (n.value === '' || Number(n.value) !== v)) {
        n.placeholder = '';
        n.value = String(v);
      }
    });
  }

  function syncPresetButtons() {
    document.querySelectorAll('#ms-presets .range-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.preset === activePreset);
    });
  }

  function buildPresets(live) {
    const liveDate = payload.as_of?.cboe || '最新';
    presets = [
      { key: 'live', label: `${liveDate} 实盘快照`, data: { ...live } },
      {
        key: 'stage1',
        label: '阶段一：离散稳态',
        data: {
          ...live,
          vix9d: 13.0,
          vix: 14.2,
          vix3m: 16.5,
          vvix: 86.0,
          skew: 136.0,
          dspx: 34.5,
          vixeq: 35.2,
          cor1m: 10.5,
          cor3m: 12.2,
          pc_equity: 0.52,
          pc_total: 0.84,
          pc_etp: 0.96,
          pc_vix: 0.72,
          pc_index: 0.98,
          pc_spx: 1.05,
          agg_gex: 3.2,
        },
      },
      {
        key: 'stage2',
        label: '阶段二：拥挤预警',
        data: {
          ...live,
          vix9d: 18.4,
          vix: 19.1,
          vix3m: 20.0,
          vvix: 112.5,
          skew: 155.0,
          dspx: 38.8,
          vixeq: 39.0,
          cor1m: 20.5,
          cor3m: 19.2,
          pc_equity: 0.65,
          pc_total: 0.96,
          pc_etp: 1.12,
          pc_vix: 0.88,
          pc_index: 1.12,
          pc_spx: 1.22,
          agg_gex: 0.4,
        },
      },
      {
        key: 'stage3',
        label: '阶段三：极端 Vanna 踩踏',
        data: {
          ...live,
          vix9d: 42.0,
          vix: 38.5,
          vix3m: 32.0,
          vvix: 142.0,
          skew: 138.0,
          dspx: 45.0,
          vixeq: 61.5,
          cor1m: 41.0,
          cor3m: 34.0,
          pc_equity: 0.86,
          pc_total: 1.16,
          pc_etp: 1.38,
          pc_vix: 1.25,
          pc_index: 1.34,
          pc_spx: 1.46,
          agg_gex: -6.5,
        },
      },
      {
        key: 'stage4',
        label: '阶段四：OPEX 重置修复',
        data: {
          ...live,
          vix9d: 17.4,
          vix: 19.2,
          vix3m: 21.4,
          vvix: 96.5,
          skew: 140.0,
          dspx: 33.5,
          vixeq: 44.0,
          cor1m: 14.5,
          cor3m: 16.0,
          pc_equity: 0.57,
          pc_total: 0.88,
          pc_etp: 1.02,
          pc_vix: 0.82,
          pc_index: 1.01,
          pc_spx: 1.12,
          agg_gex: 2.1,
        },
      },
    ];
  }

  function buildSimControls() {
    const presetHost = document.getElementById('ms-presets');
    if (presetHost) {
      presetHost.innerHTML = presets
        .map((p) => `<button type="button" class="range-btn" data-preset="${R.esc(p.key)}">${R.esc(p.label)}</button>`)
        .join('');
      presetHost.querySelectorAll('.range-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const hit = presets.find((x) => x.key === btn.dataset.preset);
          if (!hit) return;
          simInputs = { ...hit.data };
          simBase = { ...hit.data };
          activePreset = hit.key;
          Object.keys(manualOverrides).forEach((k) => delete manualOverrides[k]);
          renderAll();
        });
      });
    }

    const sliderHost = document.getElementById('ms-sliders');
    if (!sliderHost) return;
    sliderHost.innerHTML = SLIDER_SPECS.map((sp) => {
      const v = toNum(simInputs[sp.key]);
      const rangeVal = v !== null ? Math.max(sp.min, Math.min(sp.max, v)) : (sp.min + sp.max) / 2;
      const numVal = v !== null ? String(v) : '';
      const ph = v === null ? ' placeholder="缺失"' : '';
      return `
        <div class="ms-slider-item">
          <div class="ms-slider-top">
            <label class="ms-slider-label" for="ms-n-${sp.key}">${R.esc(sp.label)}</label>
            <input class="ms-slider-num" id="ms-n-${sp.key}" type="number" min="${sp.min}" max="${sp.max}" step="${sp.step}" value="${numVal}"${ph} />
          </div>
          <input class="ms-slider-range" id="ms-r-${sp.key}" type="range" aria-label="${R.esc(sp.label)}" min="${sp.min}" max="${sp.max}" step="${sp.step}" value="${rangeVal}" />
          <div class="ms-slider-hint"><span>${sp.min}${sp.unit}</span><span>${sp.max}${sp.unit}</span></div>
        </div>`;
    }).join('');

    SLIDER_SPECS.forEach((sp) => {
      const rEl = document.getElementById(`ms-r-${sp.key}`);
      const nEl = document.getElementById(`ms-n-${sp.key}`);
      if (!rEl || !nEl) return;
      let edited = false;

      rEl.addEventListener('input', (e) => {
        const num = toNum(e.target.value);
        if (num === null) return;
        simInputs[sp.key] = num;
        activePreset = 'custom';
        Object.keys(manualOverrides).forEach((k) => delete manualOverrides[k]);
        renderAll();
      });

      nEl.addEventListener('focus', () => {
        edited = false;
      });

      nEl.addEventListener('input', (e) => {
        edited = true;
        const raw = e.target.value.trim();
        if (raw === '' || raw === '-' || raw === '.' || raw === '-.') return;
        const num = toNum(raw);
        if (num === null) return;
        const clamped = Math.max(sp.min, Math.min(sp.max, num));
        simInputs[sp.key] = clamped;
        activePreset = 'custom';
        Object.keys(manualOverrides).forEach((k) => delete manualOverrides[k]);
        renderAll(sp.key);
      });

      nEl.addEventListener('blur', () => {
        if (!edited) {
          syncSliders();
          return;
        }
        edited = false;
        const raw = nEl.value.trim();
        if (raw === '') {
          syncSliders();
          return;
        }
        const num = toNum(raw);
        if (num === null) {
          syncSliders();
          return;
        }
        const clamped = Math.max(sp.min, Math.min(sp.max, num));
        simInputs[sp.key] = clamped;
        nEl.value = String(clamped);
        renderAll();
      });
    });
  }

  function setSimOpen(open) {
    simOpen = open;
    const body = document.getElementById('ms-sim-body');
    const col = document.getElementById('ms-sim-collapsed');
    const btn = document.getElementById('btn-toggle-sim');
    if (body) body.style.display = open ? 'block' : 'none';
    if (col) col.style.display = open ? 'none' : 'block';
    if (btn) {
      btn.textContent = open ? '收起推演面板' : '展开推演面板';
      btn.setAttribute('aria-expanded', String(open));
      btn.classList.toggle('active', open);
    }
  }

  function resetToLive() {
    if (!payload) return;
    simInputs = { ...payload.live_inputs };
    simBase = { ...payload.live_inputs };
    activePreset = 'live';
    Object.keys(manualOverrides).forEach((k) => delete manualOverrides[k]);
    renderAll();
  }

  function mountNotes() {
    if (notesMounted || !payload) return;
    notesMounted = true;
    const T = payload.thresholds;
    const appendFold = (id, text) => {
      const host = document.getElementById(id);
      if (host) host.appendChild(R.secNote(text, { fold: true }));
    };
    appendFold(
      'ms-score-note',
      '综合微观结构压力分 = 波动率挤兑 25% + 期限与偏度 15% + 做市商敞口 30% + 离散相关性 15% + 期权流量 15%；某数据源缺失时按其余可用维度权重比例归一化。宽基 Net GEX 采用本站 SPY/QQQ/IWM 期权链 Black-Scholes 估算，重点跟踪正负翻转与历史分位。'
    );
    appendFold(
      'ms-dealer-card',
      '零轴居中水位条：左侧红色代表做市商净负 Gamma（跌时顺势卖出、涨时追买，放大波动），右侧绿色代表净正 Gamma（高抛低吸缓冲波动）；深色竖标标注宽基历史极值底线。'
    );
    appendFold(
      'ms-disp-card',
      `VIXEQ/VIX 衡量标普 500 个股平均隐含波动率相对指数隐含波动率的比值。相关性飙升时指数波动率跳升快于个股，比值跌破 ${T.disp_risk.toFixed(2)} 通常对应离散交易平仓与宽基联动下跌。`
    );
    appendFold(
      'ms-pc-card',
      `来自 CBOE 每日市场统计接口（daily_options JSON，2019-10-07 起连续更新）。指数 P/C 常态高于个股 P/C，机构对冲先推升 SPX/Index，个股 P/C 突破 ${T.pc_equity_risk.toFixed(2)} 才往往对应全面恐慌。`
    );
    appendFold(
      'ms-hist-card',
      `左轴 VIXEQ/VIX 与右轴 COR1M 呈强负相关：每当隐含相关性急升并伴随离散比下穿 ${T.disp_risk.toFixed(2)} 红线，市场往往从结构分化切换为普跌联动。`
    );
  }

  function bindControls() {
    if (controlsBound) return;
    controlsBound = true;
    document.getElementById('btn-toggle-sim')?.addEventListener('click', () => setSimOpen(!simOpen));
    document.getElementById('btn-jump-sim')?.addEventListener('click', () => {
      setSimOpen(true);
      document.getElementById('sec-ms-sim')?.scrollIntoView({ behavior: 'smooth' });
    });
    document.getElementById('btn-reset-sim')?.addEventListener('click', resetToLive);
    document.getElementById('btn-reset-live')?.addEventListener('click', resetToLive);
  }

  function resize() {
    if (!payload || !isPaneVisible()) return;
    if (chartsDirty) {
      chartsDirty = false;
      upsertChart('ms-hist-chart', buildHistoryOption);
      renderPcChart();
      return;
    }
    ['ms-pc-chart', 'ms-hist-chart'].forEach((id) => {
      const c = R.getChart(id);
      if (c) c.resize();
    });
  }

  function getAsOf() {
    if (!payload || !payload.as_of) return null;
    const asOf = payload.as_of;
    return [
      ['CBOE', asOf.cboe, 'data/cboe/volatility.csv'],
      ['CBOE P/C', asOf.put_call, 'data/cboe/put_call.csv'],
      ['期权快照', asOf.options, 'data/options_structure'],
    ];
  }

  function getAICtxBlock() {
    if (!payload) return null;
    const ev = currentEval || payload;
    const m = payload.metrics || {};
    const dlr = payload.dealer || {};
    const asOf = payload.as_of || {};
    const isLive = activePreset === 'live';
    const presetObj = presets.find((x) => x.key === activePreset);
    const modeLabel = isLive ? '最新实盘快照' : `情景推演模式（${presetObj ? presetObj.label : '自定义参数'}，非实盘）`;
    const termStatus = !isNum(ev.vr) && !isNum(ev.sr) ? '缺数据' : ev.inverted ? '贴水倒挂' : '升水常态';
    const facts = [
      ['当前模式', modeLabel, isLive ? '' : '以下数值为假设推演参数，不代表当前实盘'],
      ['微观结构压力分', ev.score != null ? `${ev.score}/100` : '缺失', ev.stage_name || ''],
      [
        'VIX / VVIX',
        `${R.num(simInputs?.vix, 2)} / ${R.num(simInputs?.vvix, 1)}`,
        isLive && m.vix?.pctile_1y != null ? `VIX 1Y分位 ${m.vix.pctile_1y}%` : '',
      ],
      [
        'VIX/VIX3M 与 VIX9D/VIX',
        `${isNum(ev.vr) ? ev.vr.toFixed(3) : '—'} / ${isNum(ev.sr) ? ev.sr.toFixed(3) : '—'}`,
        termStatus,
      ],
      ['VIXEQ/VIX 离散比', isNum(ev.dr) ? ev.dr.toFixed(2) : '—', `COR1M ${R.num(simInputs?.cor1m, 1)}`],
      [
        'CBOE 指数 / 个股 P/C',
        `${R.num(simInputs?.pc_index, 2)} / ${R.num(simInputs?.pc_equity, 2)}`,
        `全市场 ${R.num(simInputs?.pc_total, 2)}`,
      ],
      [
        '宽基汇总 Net GEX',
        isNum(simInputs?.agg_gex) ? `$${Number(simInputs.agg_gex).toFixed(2)}B` : '—',
        dlr.hist_min != null ? `历史底线 $${dlr.hist_min.toFixed(2)}B` : '',
      ],
    ];
    return {
      title: isLive ? '期权微观结构与负反馈监控' : '期权微观结构与负反馈监控（情景推演模式）',
      src: 'CBOE 波动率指数与每日 Put/Call + 宽基期权快照（SPY/QQQ/IWM）',
      asOf: [asOf.cboe, asOf.put_call, asOf.options].filter(Boolean).sort().pop() || '',
      priority: 10,
      facts,
      note: isLive
        ? '当前为最新实盘快照；宽基 Net GEX 单位为十亿美元（B）'
        : '注意：用户当前在情景推演沙盘中修改了假设参数，以上读数为模拟结果，不是实盘收盘数据',
    };
  }

  async function mount(opts = {}) {
    if (payload) {
      resize();
      if (opts.onLoaded) opts.onLoaded(getAsOf());
      return payload;
    }
    if (mountPromise) return mountPromise;

    mountPromise = (async () => {
      const data = await R.getOpt('/api/volatility/microstructure');
      if (!data || data.error || !data.thresholds) {
        const err = new Error(data?.error || '负反馈监控快照暂不可用 · 运行 ./bin/fetch_cboe 与导出后刷新');
        const heroWrap = document.getElementById('ms-hero-wrap');
        const errBox = document.getElementById('ms-load-error');
        if (heroWrap) heroWrap.style.display = 'none';
        if (errBox) errBox.style.display = '';
        R.fail(FAIL_IDS, err);
        const jumpBtn = document.getElementById('btn-jump-sim');
        const toggleBtn = document.getElementById('btn-toggle-sim');
        const colEl = document.getElementById('ms-sim-collapsed');
        if (jumpBtn) jumpBtn.disabled = true;
        if (toggleBtn) toggleBtn.disabled = true;
        if (colEl) colEl.textContent = '底层数据暂不可用，情景推演已禁用。';
        mountPromise = null;
        return null;
      }

      // 恢复可能因前一次加载失败而隐藏的静态 Hero 骨架、按钮与图表容器
      const heroWrap = document.getElementById('ms-hero-wrap');
      const errBox = document.getElementById('ms-load-error');
      if (heroWrap) heroWrap.style.display = '';
      if (errBox) {
        errBox.style.display = 'none';
        errBox.innerHTML = '';
      }
      ['ms-pc-chart', 'ms-hist-chart'].forEach((id) => {
        const dom = document.getElementById(id);
        if (dom) dom.querySelectorAll('.re-error').forEach((n) => n.remove());
      });
      const jumpBtn = document.getElementById('btn-jump-sim');
      const toggleBtn = document.getElementById('btn-toggle-sim');
      const colEl = document.getElementById('ms-sim-collapsed');
      if (jumpBtn) jumpBtn.disabled = false;
      if (toggleBtn) toggleBtn.disabled = false;
      if (colEl) colEl.textContent = DEFAULT_SIM_COLLAPSED;

      payload = data;
      simInputs = { ...payload.live_inputs };
      simBase = { ...payload.live_inputs };
      buildPresets(payload.live_inputs);
      buildSimControls();
      mountNotes();
      bindControls();
      upsertChart('ms-hist-chart', buildHistoryOption);
      renderAll();
      if (opts.onLoaded) opts.onLoaded(getAsOf());
      return payload;
    })();

    return mountPromise;
  }

  window.OptionsMicrostructure = {
    mount,
    resize,
    getAsOf,
    getAICtxBlock,
    isLoaded: () => Boolean(payload),
  };
})();
