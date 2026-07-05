// charts-common.js — shared chart utilities

export const CHART_OPTS = {
  layout: { background: { type: 'solid', color: '#fff' }, textColor: '#333', fontSize: 12 },
  grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
  crosshair: { mode: 1 },
  rightPriceScale: { borderColor: '#e1e4e8', visible: true, autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
  leftPriceScale: { visible: false },
  timeScale: { borderColor: '#e1e4e8', timeVisible: false },
  handleScroll: { vertTouchDrag: true, mouseWheel: true },
  handleScale: { axisPressedMouseMove: true, mouseWheel: true },
};

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

export function initTooltip(chart, containerId, labels) {
  const container = document.getElementById(containerId);
  let tooltip = document.getElementById('tooltip-' + containerId) || (() => {
    const d = document.createElement('div');
    d.id = 'tooltip-' + containerId;
    d.style.cssText = 'position:absolute;display:none;background:rgba(0,0,0,0.8);color:#fff;padding:8px 12px;border-radius:6px;font-size:12px;z-index:100;pointer-events:none;white-space:nowrap';
    container.appendChild(d);
    return d;
  })();

  chart.subscribeCrosshairMove(param => {
    if (!param.point || !param.time || !param.seriesData || param.seriesData.size === 0) {
      tooltip.style.display = 'none';
      return;
    }
    let html = `<div style="color:#aaa;margin-bottom:4px">${param.time}</div>`;
    param.seriesData.forEach((sp, series) => {
      const name = series._seriesName || '';
      const label = labels[name] || '';
      const val = sp.value != null ? (typeof sp.value === 'number' ? sp.value.toFixed(2) : sp.value) : '-';
      html += `<div style="color:${sp.color || '#fff'}">${name}${label ? ` <span style="color:#aaa;font-size:11px">${label}</span>` : ''}: <b>${val}</b></div>`;
    });
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    const x = param.point.x + 12;
    const y = Math.max(param.point.y - 12, 0);
    tooltip.style.left = Math.min(x, container.offsetWidth - 160) + 'px';
    tooltip.style.top = y + 'px';
  });
}
