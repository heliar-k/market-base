// tech-view.js — K-line charts, sidebar, diagnosis, overlays

import { CHART_OPTS, addLine } from './charts-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let currentSymbol = null;
let klineData = null;
let mainChart, rsiChart, macdChart;
let candleSeries, volumeSeries;
let maSeries = {};
let rsiSeries, rsiUpper, rsiLower;
let macdHistSeries, macdLineSeries, macdSignalSeries;
let crosshairDate = null;
let debounceTimer = null;
const MA_COLORS = { MA5:'#00bcd4', MA10:'#2196f3', MA20:'#ff9800', MA60:'#9c27b0', MA120:'#9e9e9e' };
const BB_COLOR = '#78909c';

// ── init ───────────────────────────────────────────────────────────────────
export function initTechView() {
  insertInfoBar();
  initCharts();
  initControls();
  initKeyboard();
  initDiagToggle();
  loadSymbols();
}

// ── info bar ───────────────────────────────────────────────────────────────
function insertInfoBar() {
  const techView = document.querySelector('.tech-view');
  const bar = document.createElement('div');
  bar.className = 'tech-info-bar';
  bar.id = 'tech-info-bar';
  bar.innerHTML = `
    <span class="tech-symbol" id="info-symbol">--</span>
    <span class="tech-price" id="info-price">--</span>
    <span class="tech-change" id="info-change">--</span>
    <span class="tech-ohlc" id="info-ohlc">--</span>`;
  techView.insertBefore(bar, techView.firstChild);
}

function fmtVolume(v) {
  if (v == null) return '--';
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(v);
}

function updateInfoBar() {
  const sym = document.getElementById('info-symbol');
  const price = document.getElementById('info-price');
  const change = document.getElementById('info-change');
  const ohlc = document.getElementById('info-ohlc');
  if (!klineData || !klineData.length) {
    sym.textContent = currentSymbol || '--';
    price.textContent = '--';
    change.textContent = '--';
    change.className = 'tech-change';
    ohlc.textContent = '--';
    return;
  }
  const last = klineData[klineData.length - 1];
  const prev = klineData.length > 1 ? klineData[klineData.length - 2] : null;
  const chg = prev ? ((last.close - prev.close) / prev.close * 100) : 0;
  const up = chg >= 0;
  sym.textContent = currentSymbol;
  price.textContent = last.close.toFixed(2);
  change.textContent = `${up ? '▲' : '▼'} ${up ? '+' : ''}${chg.toFixed(2)}%`;
  change.className = 'tech-change ' + (up ? 'up' : 'down');
  ohlc.textContent = `O:${last.open.toFixed(2)} H:${last.high.toFixed(2)} L:${last.low.toFixed(2)} V:${fmtVolume(last.volume)}`;
}

// ── diagnosis toggle ───────────────────────────────────────────────────────
function initDiagToggle() {
  const panel = document.getElementById('diag-panel');
  const header = document.getElementById('diag-header');
  header.addEventListener('click', () => {
    panel.classList.toggle('collapsed');
    // wait for CSS transition (0.3s) then force chart resize
    setTimeout(() => {
      if (mainChart) mainChart.applyOptions({ width: document.getElementById('main-chart').clientWidth });
      if (rsiChart) rsiChart.applyOptions({ width: document.getElementById('rsi-chart').clientWidth });
      if (macdChart) macdChart.applyOptions({ width: document.getElementById('macd-chart').clientWidth });
    }, 350);
  });
}

// ── symbols sidebar ────────────────────────────────────────────────────────
const ETF_LIKE = new Set(['SPY', 'QQQ']); // grouped with indices even though type=stock

async function loadSymbols() {
  const res = await fetch('/api/symbols');
  const symbols = await res.json();
  const list = document.getElementById('symbol-list');
  list.innerHTML = '';

  const indices = symbols.filter(s => s.type === 'index' || ETF_LIKE.has(s.name));
  const stocks   = symbols.filter(s => s.type === 'stock' && !ETF_LIKE.has(s.name));

  const addSection = (title, items) => {
    const hdr = document.createElement('div');
    hdr.className = 'sidebar-section-header';
    hdr.textContent = title;
    list.appendChild(hdr);
    items.forEach(s => {
      const row = document.createElement('div');
      row.className = 'sidebar-row';
      row.dataset.name = s.name;
      row.textContent = s.name;
      row.addEventListener('click', () => selectSymbol(s.name));
      list.appendChild(row);
    });
  };

  addSection('指数 & ETF', indices);
  addSection('个股', stocks);
}

async function selectSymbol(name) {
  currentSymbol = name;
  document.querySelectorAll('.sidebar-row').forEach(el => {
    el.classList.toggle('active', el.dataset.name === name);
  });
  document.getElementById('status-symbol').textContent = name;

  const res = await fetch(`/api/kline/${name}`);
  klineData = await res.json();
  document.getElementById('status-count').textContent = `${klineData.length} 条数据`;
  if (klineData.length > 0) {
    document.getElementById('status-range').textContent =
      `${klineData[0].date} → ${klineData[klineData.length-1].date}`;
  }
  updateInfoBar();
  renderCharts();
  fetchDiag();
}

// ── charts ─────────────────────────────────────────────────────────────────
let _chartContainers = []; // ResizeObserver targets

function initCharts() {
  const mainEl = document.getElementById('main-chart');
  const rsiEl  = document.getElementById('rsi-chart');
  const macdEl = document.getElementById('macd-chart');

  mainChart = LightweightCharts.createChart(mainEl, { ...CHART_OPTS, width: mainEl.clientWidth, height: mainEl.clientHeight });
  mainChart.priceScale('right').applyOptions({ autoScale: true, scaleMargins: { top: 0.1, bottom: 0.25 } });
  candleSeries = mainChart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });
  volumeSeries = mainChart.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: 'vol',
    color: '#90caf9',
  });
  mainChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: true });

  rsiChart = LightweightCharts.createChart(rsiEl, { ...CHART_OPTS, width: rsiEl.clientWidth, height: rsiEl.clientHeight });
  rsiChart.priceScale('right').applyOptions({ minimum: 0, maximum: 100 });
  rsiSeries = rsiChart.addLineSeries({ color: '#7c4dff', lineWidth: 1.5 });
  rsiUpper = rsiChart.addLineSeries({ color: '#ef5350', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  rsiLower = rsiChart.addLineSeries({ color: '#26a69a', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });

  macdChart = LightweightCharts.createChart(macdEl, { ...CHART_OPTS, width: macdEl.clientWidth, height: macdEl.clientHeight });
  macdHistSeries = macdChart.addHistogramSeries({ priceFormat: { type: 'price', precision: 2 } });
  macdLineSeries = macdChart.addLineSeries({ color: '#2196f3', lineWidth: 1.5 });
  macdSignalSeries = macdChart.addLineSeries({ color: '#ff9800', lineWidth: 1.5 });

  // resize observer — auto-adapt when container size changes (panel toggle, window resize)
  _chartContainers.forEach(ro => ro?.disconnect());
  _chartContainers = [];
  const charts = [
    { el: mainEl, chart: mainChart },
    { el: rsiEl, chart: rsiChart },
    { el: macdEl, chart: macdChart },
  ];
  charts.forEach(({ el, chart }) => {
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        const w = e.contentRect.width;
        const h = e.contentRect.height;
        if (w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
      }
    });
    ro.observe(el);
    _chartContainers.push(ro);
  });

  // sync time scales
  const syncFrom = (src, targets) => {
    src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      targets.forEach(t => t.timeScale().setVisibleLogicalRange(range));
    });
  };
  syncFrom(mainChart, [rsiChart, macdChart]);
  syncFrom(rsiChart, [mainChart, macdChart]);
  syncFrom(macdChart, [mainChart, rsiChart]);

  // crosshair sync → diag
  mainChart.subscribeCrosshairMove(param => {
    if (!param || !param.time) { crosshairDate = null; return; }
    crosshairDate = param.time;
    syncCrosshairToChart(rsiChart, param);
    syncCrosshairToChart(macdChart, param);
    debouncedDiag();
  });
}

function syncCrosshairToChart(chart, param) {
  chart.setCrosshairPosition(NaN, NaN, param.time);
}

function renderCharts() {
  if (!klineData || !klineData.length) return;
  const candles = klineData.map(d => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close }));
  const volumes = klineData.map(d => ({
    time: d.date,
    value: d.volume ?? 0,
    color: d.close >= d.open ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)',
  }));
  candleSeries.setData(candles);
  volumeSeries.setData(volumes);

  // RSI
  const rsi = klineData.filter(d => d.RSI != null).map(d => ({ time: d.date, value: d.RSI }));
  rsiSeries.setData(rsi);
  if (rsi.length) {
    const times = rsi.map(d => d.time);
    rsiUpper.setData(times.map(t => ({ time: t, value: 70 })));
    rsiLower.setData(times.map(t => ({ time: t, value: 30 })));
  }

  // MACD
  const hist = klineData.filter(d => d.MACD_hist != null).map(d => ({
    time: d.date, value: d.MACD_hist,
    color: d.MACD_hist >= 0 ? '#26a69a' : '#ef5350',
  }));
  const macdLine = klineData.filter(d => d.MACD != null).map(d => ({ time: d.date, value: d.MACD }));
  const signalLine = klineData.filter(d => d.MACD_signal != null).map(d => ({ time: d.date, value: d.MACD_signal }));
  macdHistSeries.setData(hist);
  macdLineSeries.setData(macdLine);
  macdSignalSeries.setData(signalLine);

  refreshOverlays();
  mainChart.timeScale().fitContent();
}

// ── overlays ───────────────────────────────────────────────────────────────
function initControls() {
  document.querySelectorAll('#controls input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', refreshOverlays);
  });
}

function refreshOverlays() {
  if (!klineData) return;
  Object.values(maSeries).forEach(s => { try { mainChart.removeSeries(s); } catch(e){} });
  maSeries = {};

  const active = [...document.querySelectorAll('#controls input[type="checkbox"]:checked')].map(c => c.dataset.ma);

  active.forEach(key => {
    if (key === 'BB') {
      const upper = klineData.filter(d => d.BB_upper != null).map(d => ({ time: d.date, value: d.BB_upper }));
      const lower = klineData.filter(d => d.BB_lower != null).map(d => ({ time: d.date, value: d.BB_lower }));
      const mid = klineData.filter(d => d.BB_mid != null).map(d => ({ time: d.date, value: d.BB_mid }));
      maSeries['BB_upper'] = addLine(mainChart, upper, BB_COLOR, 1, 2);
      maSeries['BB_lower'] = addLine(mainChart, lower, BB_COLOR, 1, 2);
      maSeries['BB_mid'] = addLine(mainChart, mid, BB_COLOR, 1, 0);
    } else if (key === 'ST') {
      const stUp = klineData.filter(d => d.SUPERT_dir === 1 && d.SUPERT != null).map(d => ({ time: d.date, value: d.SUPERT }));
      const stDn = klineData.filter(d => d.SUPERT_dir === -1 && d.SUPERT != null).map(d => ({ time: d.date, value: d.SUPERT }));
      maSeries['ST_up'] = addLine(mainChart, stUp, '#26a69a', 2, 0);
      maSeries['ST_dn'] = addLine(mainChart, stDn, '#ef5350', 2, 0);
    } else {
      const data = klineData.filter(d => d[key] != null).map(d => ({ time: d.date, value: d[key] }));
      maSeries[key] = addLine(mainChart, data, MA_COLORS[key] || '#333', 1.5, 0);
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
  let url = `/api/diag/${currentSymbol}`;
  if (crosshairDate) url += `?as_of=${crosshairDate}`;
  try {
    const res = await fetch(url);
    const d = await res.json();
    renderDiag(d);
  } catch(e) {
    document.getElementById('diag-content').innerHTML = `<div class="loading">加载失败</div>`;
  }
}

function renderDiag(d) {
  const el = document.getElementById('diag-content');
  const maxScore = 20;
  const pct = Math.max(0, Math.min(100, ((d.total_score + maxScore) / (2 * maxScore)) * 100));
  const scoreColor = d.total_score > 5 ? '#26a69a' : d.total_score < -5 ? '#ef5350' : '#ff9800';

  let html = `
    <div class="diag-section">
      <div style="display:flex;align-items:baseline;gap:12px">
        <span class="score-text" style="color:${scoreColor}">${d.total_score > 0 ? '+' : ''}${d.total_score}</span>
        <span style="color:#666;font-size:13px">${d.last_date}</span>
      </div>
      <div class="score-bar"><div class="score-fill" style="width:${pct}%;background:${scoreColor}"></div></div>
      <div style="font-size:20px;font-weight:600">¥${d.last_price?.toFixed(2) ?? '—'}</div>
    </div>

    <div class="diag-section">
      <h4>均线</h4>
      ${Object.entries(d.ma_signals || {}).map(([k, v]) => `
        <div class="diag-row">
          <span class="diag-label">${k}</span>
          <span class="diag-value">
            ${(d.ma_values?.[k] ?? '—')}
            <span class="${v === 'above' ? 'up' : 'down'}">${v === 'above' ? '↑' : '↓'}</span>
          </span>
        </div>
      `).join('')}
    </div>

    <div class="diag-section">
      <h4>RSI</h4>
      <div class="diag-row">
        <span class="diag-label">RSI(14)</span>
        <span class="diag-value ${d.RSI < 30 ? 'up' : d.RSI > 70 ? 'down' : 'neutral'}">${d.RSI?.toFixed(1) ?? '—'} (${d.RSI_detail || ''})</span>
      </div>
    </div>

    <div class="diag-section">
      <h4>MACD</h4>
      <div class="diag-row"><span class="diag-label">MACD</span><span class="diag-value">${d.MACD?.toFixed(3) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Signal</span><span class="diag-value">${d.MACD_signal?.toFixed(3) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Histogram</span><span class="diag-value">${d.MACD_hist?.toFixed(3) ?? '—'}</span></div>
      <div class="diag-row">
        <span class="diag-label">状态</span>
        <span class="diag-value ${d.MACD_status === 'golden_cross' ? 'up' : 'down'}">
          ${d.MACD_status === 'golden_cross' ? '✦ 金叉' : '✦ 死叉'}
        </span>
      </div>
    </div>

    <div class="diag-section">
      <h4>ADX / DMI</h4>
      <div class="diag-row"><span class="diag-label">ADX</span><span class="diag-value">${d.ADX?.toFixed(1) ?? '—'} (${d.ADX_trend || ''})</span></div>
      <div class="diag-row"><span class="diag-label">+DI</span><span class="diag-value up">${d.DMP?.toFixed(1) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">-DI</span><span class="diag-value down">${d.DMN?.toFixed(1) ?? '—'}</span></div>
    </div>

    <div class="diag-section">
      <h4>布林带</h4>
      <div class="diag-row"><span class="diag-label">Upper</span><span class="diag-value">${d.BB_upper?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Mid</span><span class="diag-value">${d.BB_mid?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Lower</span><span class="diag-value">${d.BB_lower?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">位置</span><span class="diag-value">${d.BB_position != null ? d.BB_position.toFixed(1) + '%' : '—'}</span></div>
    </div>

    <div class="diag-section">
      <h4>支撑 / 阻力</h4>
      <div class="diag-row"><span class="diag-label">阻力 (90d)</span><span class="diag-value down">${d.resistance_90d?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">支撑 (90d)</span><span class="diag-value up">${d.support_90d?.toFixed(2) ?? '—'}</span></div>
    </div>

    <div class="diag-section">
      <h4>其它指标</h4>
      <div class="diag-row"><span class="diag-label">ATR</span><span class="diag-value">${d.ATR?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">量比</span><span class="diag-value">${d.vol_ratio?.toFixed(2) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">Stoch K/D</span><span class="diag-value">${d.STOCH_k?.toFixed(1) ?? '—'} / ${d.STOCH_d?.toFixed(1) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">CCI</span><span class="diag-value">${d.CCI?.toFixed(1) ?? '—'} (${d.CCI_detail || ''})</span></div>
      <div class="diag-row"><span class="diag-label">MFI</span><span class="diag-value">${d.MFI?.toFixed(1) ?? '—'}</span></div>
      <div class="diag-row"><span class="diag-label">OBV 背离</span><span class="diag-value">${d.OBV_divergence || '无'}</span></div>
    </div>

    <div class="diag-section">
      <h4>涨跌幅</h4>
      ${Object.entries(d.changes || {}).map(([k, v]) => `
        <div class="diag-row">
          <span class="diag-label">${k}</span>
          <span class="diag-value ${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%</span>
        </div>
      `).join('')}
    </div>

    <div class="diag-section">
      <h4>K线形态</h4>
      <div class="cdl-list">
        ${(d.cdl_bullish || []).map(n => `<span class="tag tag-bull">${n}</span>`).join('')}
        ${(d.cdl_bearish || []).map(n => `<span class="tag tag-bear">${n}</span>`).join('')}
        ${!(d.cdl_bullish?.length || d.cdl_bearish?.length) ? '<span style="color:#999;font-size:12px">无信号</span>' : ''}
      </div>
    </div>

    <div class="diag-section">
      <h4>评分明细</h4>
      ${(d.scores || []).map(s => `
        <div class="diag-row">
          <span class="diag-label">${s.label}</span>
          <span class="diag-value ${s.value > 0 ? 'up' : s.value < 0 ? 'down' : 'neutral'}">${s.value > 0 ? '+' : ''}${s.value}</span>
        </div>
      `).join('')}
    </div>
  `;
  el.innerHTML = html;
}

// ── keyboard ───────────────────────────────────────────────────────────────
function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (!klineData || !klineData.length) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const ts = mainChart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (!range) return;
      const totalBars = klineData.length;
      let idx = crosshairDate ? klineData.findIndex(d => d.date === crosshairDate) : Math.floor(totalBars / 2);
      idx += e.key === 'ArrowRight' ? 1 : -1;
      idx = Math.max(0, Math.min(totalBars - 1, idx));
      crosshairDate = klineData[idx].date;
      mainChart.setCrosshairPosition(klineData[idx].close, idx, candleSeries);
      debouncedDiag();
    }
  });
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = currentSymbol || '—';
  if (klineData && klineData.length) {
    document.getElementById('status-count').textContent = `${klineData.length} 条数据`;
    document.getElementById('status-range').textContent =
      `${klineData[0].date} → ${klineData[klineData.length-1].date}`;
  } else {
    document.getElementById('status-count').textContent = '';
    document.getElementById('status-range').textContent = '';
  }
}
