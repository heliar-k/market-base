// charts-common.js — shared chart utilities（lightweight-charts，SPA K线专用；
// 非 ECharts 库，不并入 ECharts 主题，但色值统一回读 tokens.css / app.css 的 CSS 变量）

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

export const CHART_OPTS = {
  layout: { background: { type: 'solid', color: cssVar('--surface') }, textColor: cssVar('--text'), fontSize: 12 },
  grid: { vertLines: { color: cssVar('--border-light') }, horzLines: { color: cssVar('--border-light') } },
  crosshair: { mode: 1 },
  rightPriceScale: { borderColor: cssVar('--border'), visible: true, autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
  leftPriceScale: { visible: false },
  timeScale: { borderColor: cssVar('--border'), timeVisible: false },
  handleScroll: { vertTouchDrag: true, mouseWheel: false },
  handleScale: { axisPressedMouseMove: true, mouseWheel: false },
};

// theme-changed 时重新应用（读取的是切换后的当前主题变量，亮暗均适用；名字保留兼容 tech-view 调用点）
export function darkChartOpts() {
  return {
    layout: { background: { type: 'solid', color: cssVar('--surface') }, textColor: cssVar('--text'), fontSize: 12 },
    grid: { vertLines: { color: cssVar('--border-light') }, horzLines: { color: cssVar('--border-light') } },
    rightPriceScale: { borderColor: cssVar('--border') },
    timeScale: { borderColor: cssVar('--border') },
  };
}

export function addLine(chart, data, color, width, style, showLabels) {
  const s = chart.addLineSeries({
    color, lineWidth: width, lineStyle: style,
    priceLineVisible: !!showLabels,
    lastValueVisible: !!showLabels,
    crosshairMarkerVisible: !!showLabels,
  });
  s.setData(data);
  return s;
}
