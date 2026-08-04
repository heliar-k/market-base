// charts-common.js — shared chart utilities

export const CHART_OPTS = {
  layout: { background: { type: 'solid', color: '#fff' }, textColor: '#333', fontSize: 12 },
  grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
  crosshair: { mode: 1 },
  rightPriceScale: { borderColor: '#e1e4e8', visible: true, autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
  leftPriceScale: { visible: false },
  timeScale: { borderColor: '#e1e4e8', timeVisible: false },
  handleScroll: { vertTouchDrag: true, mouseWheel: false },
  handleScale: { axisPressedMouseMove: true, mouseWheel: false },
};

export function darkChartOpts() {
  return {
    layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#c9d1d9', fontSize: 12 },
    grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d' },
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
