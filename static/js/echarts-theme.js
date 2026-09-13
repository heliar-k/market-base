// echarts-theme.js — 全站 ECharts 主题注册唯一入口（Phase 2 单轨化）。
// 经典脚本：SPA（index.html）与专题页（rates-common.js 链路）共用同一文件、同一 registerTheme；
// 不再作为 ES module 被 import（dashboard.js / cross-correlation.js 直接用全局函数）。
// 色值一律从 tokens.css / app.css 的 CSS 变量回读（getComputedStyle），本文件禁止颜色字面量。

// 读取当前主题下的 CSS 变量值（body.dark 覆写自动生效）
function reCssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

// 序列色板（8 色）：前 4 位为语义色 token，后 4 位为分类色 token
function RE_CHART_COLORS() {
  return [
    reCssVar('--color-brand'), reCssVar('--color-warn'),
    reCssVar('--color-up'), reCssVar('--color-down'),
    reCssVar('--chart-purple'), reCssVar('--chart-cyan'),
    reCssVar('--chart-violet'), reCssVar('--chart-slate'),
  ];
}

// 主题对象：文字/描边/底色全部走结构性 tokens（app.css :root / body.dark）
function buildTheme() {
  const text = reCssVar('--text');
  const textSecondary = reCssVar('--text-secondary');
  const textMuted = reCssVar('--text-dim');
  const borderColor = reCssVar('--border');
  const borderSubtle = reCssVar('--border-light');
  const bg = reCssVar('--surface');
  const axisLabel = { color: textMuted, fontSize: 10 };
  const splitLine = { lineStyle: { color: borderSubtle } };
  return {
    backgroundColor: 'transparent',
    textStyle: { color: textSecondary, fontFamily: "system-ui, -apple-system, sans-serif", fontSize: 12 },
    title: { textStyle: { color: text, fontSize: 14, fontWeight: 600 }, subtextStyle: { color: textMuted, fontSize: 11 } },
    legend: { textStyle: { color: textSecondary, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
    tooltip: {
      backgroundColor: bg, borderColor: borderColor, borderWidth: 1,
      textStyle: { color: text, fontSize: 12, fontFamily: "'SF Mono', ui-monospace, monospace" },
      extraCssText: 'border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.12);',
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: borderColor } }, axisTick: { lineStyle: { color: borderColor } },
      axisLabel: axisLabel, splitLine: splitLine,
    },
    valueAxis: {
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: axisLabel, splitLine: splitLine,
    },
    timeAxis: {
      axisLine: { lineStyle: { color: borderColor } }, axisTick: { lineStyle: { color: borderColor } },
      axisLabel: axisLabel, splitLine: splitLine,
    },
    logAxis: { axisLabel: axisLabel, splitLine: splitLine },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 48, containLabel: true },
    color: RE_CHART_COLORS(),
    line: { lineStyle: { width: 2 }, symbol: 'none' },
    bar: { itemStyle: { borderRadius: [2, 2, 0, 0] } },
  };
}

// 按当前亮暗状态注册主题。CSS 变量只在“当前主题”下可取，故 macro/macroDark 注册同一份
// 当前值：两个名字只是 SPA 既有 init 调用的兼容占位，调用方总是配对的当前主题。
// 主题切换后需重新调用本函数（theme-changed 链路里 rates-common / cross-correlation 均已处理）。
function registerMacroTheme() {
  if (typeof echarts === 'undefined' || !document.body) return;
  const theme = buildTheme();
  echarts.registerTheme('macro', theme);
  echarts.registerTheme('macroDark', theme);
}

/** Re-render an existing ECharts instance with the current dark/light theme.
 *  Disposes old chart and re-inits with same options — state (zoom) is lost
 *  but this is the only reliable way to change ECharts theme without full re-init. */
function reThemeECharts(chart, dom, opts) {
  const dark = document.body.classList.contains('dark');
  chart.dispose();
  const next = echarts.init(dom, dark ? 'macroDark' : 'macro');
  next.setOption(opts);
  return next;
}
