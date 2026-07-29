// echarts-theme.js — ECharts shared theme & utilities
// Design tokens read from CSS custom properties, matching timsun.net's approach

export function registerMacroTheme() {
  if (typeof echarts === 'undefined') return;

  const rootStyle = getComputedStyle(document.documentElement);
  const bg = rootStyle.getPropertyValue('--chart-bg') || '#ffffff';
  const textPrimary = '#1a1a2e';
  const textSecondary = '#666666';
  const textMuted = '#999999';
  const borderColor = '#e1e4e8';
  const borderSubtle = '#f0f0f0';
  const brand = '#1a73e8';
  const positive = '#26a69a';
  const negative = '#ef5350';

  echarts.registerTheme('macro', {
    backgroundColor: 'transparent',
    textStyle: {
      color: textSecondary,
      fontFamily: "system-ui, -apple-system, sans-serif",
      fontSize: 12,
    },
    title: {
      textStyle: { color: textPrimary, fontSize: 14, fontWeight: 600 },
      subtextStyle: { color: textMuted, fontSize: 11 },
    },
    legend: {
      textStyle: { color: textSecondary, fontSize: 11 },
      itemWidth: 14, itemHeight: 8,
    },
    tooltip: {
      backgroundColor: '#fff',
      borderColor: borderColor,
      borderWidth: 1,
      textStyle: {
        color: textPrimary,
        fontSize: 12,
        fontFamily: "'SF Mono', ui-monospace, monospace",
      },
      extraCssText: 'border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.12);',
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: borderColor } },
      axisTick: { lineStyle: { color: borderColor } },
      axisLabel: { color: textMuted, fontSize: 10 },
      splitLine: { lineStyle: { color: borderSubtle } },
    },
    valueAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textMuted, fontSize: 10 },
      splitLine: { lineStyle: { color: borderSubtle } },
    },
    timeAxis: {
      axisLine: { lineStyle: { color: borderColor } },
      axisTick: { lineStyle: { color: borderColor } },
      axisLabel: { color: textMuted, fontSize: 10 },
      splitLine: { lineStyle: { color: borderSubtle } },
    },
    logAxis: {
      axisLabel: { color: textMuted, fontSize: 10 },
      splitLine: { lineStyle: { color: borderSubtle } },
    },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 48, containLabel: true },
    color: [brand, '#ff9800', positive, negative, '#9c27b0', '#00bcd4', '#ff5722', '#607d8b'],
    line: {
      lineStyle: { width: 2 },
      symbol: 'none',
    },
    bar: {
      itemStyle: { borderRadius: [2, 2, 0, 0] },
    },
  });
}
