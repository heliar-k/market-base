// echarts-theme.js — ECharts shared theme & utilities

export function registerMacroTheme() {
  if (typeof echarts === 'undefined') return;

  echarts.registerTheme('macro', buildTheme(false));
  echarts.registerTheme('macroDark', buildTheme(true));
}

function buildTheme(dark) {
  const t = dark
    ? { textPrimary: '#c9d1d9', textSecondary: '#8b949e', textMuted: '#484f58',
        borderColor: '#30363d', borderSubtle: '#21262d', bg: '#161b22' }
    : { textPrimary: '#1a1a2e', textSecondary: '#666666', textMuted: '#999999',
        borderColor: '#e1e4e8', borderSubtle: '#f0f0f0', bg: '#ffffff' };

  return {
    backgroundColor: 'transparent',
    textStyle: { color: t.textSecondary, fontFamily: "system-ui, -apple-system, sans-serif", fontSize: 12 },
    title: { textStyle: { color: t.textPrimary, fontSize: 14, fontWeight: 600 }, subtextStyle: { color: t.textMuted, fontSize: 11 } },
    legend: { textStyle: { color: t.textSecondary, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
    tooltip: {
      backgroundColor: t.bg, borderColor: t.borderColor, borderWidth: 1,
      textStyle: { color: t.textPrimary, fontSize: 12, fontFamily: "'SF Mono', ui-monospace, monospace" },
      extraCssText: 'border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.12);',
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: t.borderColor } }, axisTick: { lineStyle: { color: t.borderColor } },
      axisLabel: { color: t.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: t.borderSubtle } },
    },
    valueAxis: {
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: t.borderSubtle } },
    },
    timeAxis: {
      axisLine: { lineStyle: { color: t.borderColor } }, axisTick: { lineStyle: { color: t.borderColor } },
      axisLabel: { color: t.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: t.borderSubtle } },
    },
    logAxis: {
      axisLabel: { color: t.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: t.borderSubtle } },
    },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 48, containLabel: true },
    color: ['#1a73e8', '#ff9800', '#26a69a', '#ef5350', '#9c27b0', '#00bcd4', '#7c4dff', '#607d8b'],
    line: { lineStyle: { width: 2 }, symbol: 'none' },
    bar: { itemStyle: { borderRadius: [2, 2, 0, 0] } },
  };
}

/** Re-render an existing ECharts instance with the current dark/light theme.
 *  Disposes old chart and re-inits with same options — state (zoom) is lost
 *  but this is the only reliable way to change ECharts theme without full re-init. */
export function reThemeECharts(chart, dom, opts) {
  const dark = document.body.classList.contains('dark');
  chart.dispose();
  const next = echarts.init(dom, dark ? 'macroDark' : 'macro');
  next.setOption(opts);
  return next;
}
