// stock-kline.js — individual stock K-line view with candlestick + indicators

import { CHART_OPTS, addLine } from './charts-common.js';

// ── state ──────────────────────────────────────────────────────────────────
let currentSymbol = 'AAPL';
let klineData = null;
let indicatorData = null;
let currentInterval = '1d';
let currentDays = 365;

let mainChart, volumeChart, macdChart, rsiChart;
let candleSeries, volumeSeries;
let volChartSeries = null;
let maSeries = {};
let bbSeries = {};
let macdHistSeries, macdLineSeries, macdSignalSeries;
let rsiSeries, rsi70, rsi30;

const activeIndicators = new Set(['MA5', 'MA20']);
const allCharts = [];

const COLORS = {
  MA5: '#2196f3', MA20: '#ff9800', MA60: '#9c27b0', MA120: '#607d8b',
  BB: 'rgba(33,150,243,0.2)', BB_line: '#2196f3',
};

const STOCK_LIST = ['AAPL','AMZN','GOOG','META','MSFT','MU','NVDA','QQQ','SPY','TSLA','TSM'];

// ── init ───────────────────────────────────────────────────────────────────
export async function initStockView() {
  const root = document.getElementById('stock-view');
  root.innerHTML = '';
  root.style.cssText = 'flex:1;flex-direction:column;gap:8px';

  root.appendChild(buildToolbar());
  root.appendChild(chartCard('stock-main-chart', 420));
  root.appendChild(chartCard('stock-volume-chart', 140));
  root.appendChild(chartCard('stock-macd-chart', 160));
  root.appendChild(chartCard('stock-rsi-chart', 140));

  await loadStockList();
  initCharts();
  // ponytail: auto-select stock when jumping from dashboard watchlist
  const pending = window._pendingStockSymbol;
  if (pending && STOCK_LIST.includes(pending)) {
    currentSymbol = pending;
    window._pendingStockSymbol = null;
  }
  await selectStock(currentSymbol);
}

// ── toolbar ────────────────────────────────────────────────────────────────
function buildToolbar() {
  const bar = document.createElement('div');
  bar.className = 'controls stock-toolbar';

  // Stock buttons
  const stockGroup = document.createElement('div');
  stockGroup.className = 'stock-btn-group';
  STOCK_LIST.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'stock-btn' + (s === currentSymbol ? ' active' : '');
    btn.textContent = s;
    btn.dataset.symbol = s;
    btn.addEventListener('click', () => selectStock(s));
    stockGroup.appendChild(btn);
  });

  // Interval buttons
  const intervalGroup = document.createElement('div');
  intervalGroup.className = 'stock-btn-group';
  [['1d','1日'],['1wk','1周'],['1mo','1月']].forEach(([val, label]) => {
    const btn = document.createElement('button');
    btn.className = 'stock-btn' + (val === currentInterval ? ' active' : '');
    btn.textContent = label;
    btn.dataset.interval = val;
    btn.addEventListener('click', () => selectInterval(val));
    intervalGroup.appendChild(btn);
  });

  // Days buttons
  const daysGroup = document.createElement('div');
  daysGroup.className = 'stock-btn-group';
  [[90,'90天'],[365,'1年'],[0,'全部']].forEach(([val, label]) => {
    const btn = document.createElement('button');
    btn.className = 'stock-btn' + (val === currentDays ? ' active' : '');
    btn.textContent = label;
    btn.dataset.days = val;
    btn.addEventListener('click', () => selectDays(val));
    daysGroup.appendChild(btn);
  });

  // Indicator toggles
  const indGroup = document.createElement('div');
  indGroup.className = 'stock-btn-group';
  ['MA5','MA20','MA60','MA120','BB','MACD','RSI'].forEach(name => {
    const label = document.createElement('label');
    label.className = 'stock-indicator-label';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.indicator = name;
    cb.checked = activeIndicators.has(name);
    cb.addEventListener('change', () => {
      cb.checked ? activeIndicators.add(name) : activeIndicators.delete(name);
      refreshOverlays();
    });
    const span = document.createElement('span');
    span.style.color = COLORS[name] || '#333';
    span.textContent = name;
    label.appendChild(cb);
    label.appendChild(span);
    indGroup.appendChild(label);
  });

  bar.appendChild(stockGroup);
  bar.appendChild(separator());
  bar.appendChild(intervalGroup);
  bar.appendChild(separator());
  bar.appendChild(daysGroup);
  bar.appendChild(separator());
  bar.appendChild(indGroup);
  return bar;
}

function separator() {
  const s = document.createElement('span');
  s.style.cssText = 'width:1px;height:20px;background:#e1e4e8;margin:0 4px';
  return s;
}

function chartCard(id, height) {
  const card = document.createElement('div');
  card.className = 'chart-card';
  const inner = document.createElement('div');
  inner.id = id;
  inner.style.height = height + 'px';
  card.appendChild(inner);
  return card;
}

// ── data loading ───────────────────────────────────────────────────────────
async function loadStockList() {
  // Use hardcoded list; API available at /api/stocks if needed later
}

async function loadKlineData() {
  const daysParam = currentDays > 0 ? currentDays : 9999;
  const res = await fetch(`/api/kline/${currentSymbol}?interval=${currentInterval}&days=${daysParam}`);
  klineData = await res.json();

  const indRes = await fetch(`/api/kline/${currentSymbol}/indicators?days=${daysParam}`);
  const indJson = await indRes.json();
  indicatorData = indJson.data;
}

// ── charts ─────────────────────────────────────────────────────────────────
function initCharts() {
  destroyCharts();

  // Main candlestick + volume overlay
  mainChart = LightweightCharts.createChart(
    document.getElementById('stock-main-chart'),
    { ...CHART_OPTS, height: 420,
      handleScroll: { vertTouchDrag: true, mouseWheel: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: false },
    }
  );
  mainChart.priceScale('right').applyOptions({
    autoScale: true, scaleMargins: { top: 0.05, bottom: 0.2 }
  });

  candleSeries = mainChart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });

  volumeSeries = mainChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  mainChart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 }, visible: false,
  });

  // Volume chart (separate for clarity)
  volumeChart = LightweightCharts.createChart(
    document.getElementById('stock-volume-chart'),
    { ...CHART_OPTS, height: 140,
      handleScroll: { vertTouchDrag: true, mouseWheel: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: false },
    }
  );

  // MACD chart
  macdChart = LightweightCharts.createChart(
    document.getElementById('stock-macd-chart'),
    { ...CHART_OPTS, height: 160,
      handleScroll: { vertTouchDrag: true, mouseWheel: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: false },
    }
  );
  macdHistSeries = macdChart.addHistogramSeries({
    priceFormat: { type: 'price', precision: 3 },
  });
  macdLineSeries = macdChart.addLineSeries({ color: '#2196f3', lineWidth: 1.5 });
  macdSignalSeries = macdChart.addLineSeries({ color: '#ff9800', lineWidth: 1.5 });

  // RSI chart
  rsiChart = LightweightCharts.createChart(
    document.getElementById('stock-rsi-chart'),
    { ...CHART_OPTS, height: 140,
      handleScroll: { vertTouchDrag: true, mouseWheel: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: false },
    }
  );
  rsiChart.priceScale('right').applyOptions({ minimum: 0, maximum: 100 });
  rsiSeries = rsiChart.addLineSeries({ color: '#4caf50', lineWidth: 1.5 });
  rsi70 = rsiChart.addLineSeries({
    color: 'rgba(255,100,100,0.3)', lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false,
  });
  rsi30 = rsiChart.addLineSeries({
    color: 'rgba(100,255,100,0.3)', lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false,
  });

  allCharts.length = 0;
  allCharts.push(mainChart, volumeChart, macdChart, rsiChart);
  syncTimeScales();
  syncCrosshairs();
}

function destroyCharts() {
  allCharts.forEach(c => { try { c.remove(); } catch(e){} });
  allCharts.length = 0;
  maSeries = {};
  bbSeries = {};
  volChartSeries = null;
}

// ── render ─────────────────────────────────────────────────────────────────
function renderData() {
  if (!klineData || !klineData.length) return;

  // Candlestick
  const candles = klineData.map(d => ({
    time: d.date, open: d.open, high: d.high, low: d.low, close: d.close,
  }));
  candleSeries.setData(candles);

  // Volume overlay on main chart
  const volumes = klineData.map(d => ({
    time: d.date, value: d.volume ?? 0,
    color: d.close >= d.open ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)',
  }));
  volumeSeries.setData(volumes);

  // Separate volume chart
  if (!volChartSeries) {
    volChartSeries = volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' } });
  }
  volChartSeries.setData(volumes);

  // MACD from indicator data
  if (indicatorData) {
    const hist = indicatorData.filter(d => d.MACD_Hist != null).map(d => ({
      time: d.date, value: d.MACD_Hist,
      color: d.MACD_Hist >= 0 ? '#2196f3' : '#ef5350',
    }));
    const line = indicatorData.filter(d => d.MACD != null).map(d => ({
      time: d.date, value: d.MACD,
    }));
    const signal = indicatorData.filter(d => d.MACD_Signal != null).map(d => ({
      time: d.date, value: d.MACD_Signal,
    }));
    macdHistSeries.setData(hist);
    macdLineSeries.setData(line);
    macdSignalSeries.setData(signal);

    // RSI
    const rsi = indicatorData.filter(d => d.RSI != null).map(d => ({
      time: d.date, value: d.RSI,
    }));
    rsiSeries.setData(rsi);
    if (rsi.length) {
      const times = rsi.map(d => d.time);
      rsi70.setData(times.map(t => ({ time: t, value: 70 })));
      rsi30.setData(times.map(t => ({ time: t, value: 30 })));
    }
  }

  refreshOverlays();
  mainChart.timeScale().fitContent();
}

// ── overlays (MA + BB on main chart) ───────────────────────────────────────
function refreshOverlays() {
  if (!indicatorData) return;

  // Clean previous overlay series
  [...Object.values(maSeries), ...Object.values(bbSeries)].forEach(s => {
    try { mainChart.removeSeries(s); } catch(e){}
  });
  maSeries = {};
  bbSeries = {};

  // MA lines
  ['MA5','MA20','MA60','MA120'].forEach(key => {
    if (!activeIndicators.has(key)) return;
    const data = indicatorData
      .filter(d => d[key] != null)
      .map(d => ({ time: d.date, value: d[key] }));
    if (data.length) {
      maSeries[key] = addLine(mainChart, data, COLORS[key], 1.5, 0);
    }
  });

  // Bollinger Bands
  if (activeIndicators.has('BB')) {
    const upper = indicatorData.filter(d => d.BB_Upper != null).map(d => ({ time: d.date, value: d.BB_Upper }));
    const mid = indicatorData.filter(d => d.BB_Mid != null).map(d => ({ time: d.date, value: d.BB_Mid }));
    const lower = indicatorData.filter(d => d.BB_Lower != null).map(d => ({ time: d.date, value: d.BB_Lower }));
    if (upper.length) {
      bbSeries.upper = addLine(mainChart, upper, COLORS.BB_line, 1, 2);
      bbSeries.mid = addLine(mainChart, mid, COLORS.BB_line, 1, 0);
      bbSeries.lower = addLine(mainChart, lower, COLORS.BB_line, 1, 2);
    }
  }
}

// ── time scale sync ────────────────────────────────────────────────────────
function syncTimeScales() {
  let syncing = false;
  allCharts.forEach(src => {
    src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      allCharts.forEach(t => {
        if (t !== src) t.timeScale().setVisibleLogicalRange(range);
      });
      syncing = false;
    });
  });
}

// ── crosshair sync ─────────────────────────────────────────────────────────
// ponytail: uses NaN price + time; works because all charts share the same time scale
function syncCrosshairs() {
  let syncing = false;
  allCharts.forEach(chart => {
    chart.subscribeCrosshairMove(param => {
      if (syncing) return;
      if (!param || !param.time) return;
      syncing = true;
      allCharts.forEach(other => {
        if (other !== chart) other.setCrosshairPosition(NaN, NaN, param.time);
      });
      syncing = false;
    });
  });
}

// ── actions ────────────────────────────────────────────────────────────────
async function selectStock(symbol) {
  currentSymbol = symbol;
  document.querySelectorAll('.stock-btn[data-symbol]').forEach(b =>
    b.classList.toggle('active', b.dataset.symbol === symbol));
  document.getElementById('status-symbol').textContent = symbol;

  await loadKlineData();
  renderData();
  updateStatus();
}

async function selectInterval(val) {
  currentInterval = val;
  document.querySelectorAll('.stock-btn[data-interval]').forEach(b =>
    b.classList.toggle('active', b.dataset.interval === val));
  await loadKlineData();
  renderData();
  updateStatus();
}

async function selectDays(val) {
  currentDays = val;
  document.querySelectorAll('.stock-btn[data-days]').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.days) === val));
  await loadKlineData();
  renderData();
  updateStatus();
}

// ── cleanup (called on tab switch) ─────────────────────────────────────────
export function cleanup() {
  destroyCharts();
}

// ── status ─────────────────────────────────────────────────────────────────
export function updateStatus() {
  document.getElementById('status-symbol').textContent = currentSymbol || '—';
  if (klineData && klineData.length) {
    document.getElementById('status-count').textContent = `${klineData.length} 条数据`;
    document.getElementById('status-range').textContent =
      `${klineData[0].date} → ${klineData[klineData.length - 1].date}`;
  } else {
    document.getElementById('status-count').textContent = '';
    document.getElementById('status-range').textContent = '';
  }
}
