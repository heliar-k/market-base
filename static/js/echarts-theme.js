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
  const bg = reCssVar('--tooltip-bg'); // 浮层层（surface-alt 半透明化，配合 blur 制造纵深）
  const mono = reCssVar('--font-mono');
  const axisLabel = { color: textMuted, fontSize: 10, fontFamily: mono };
  const splitLine = { lineStyle: { color: borderSubtle } };
  return {
    backgroundColor: 'transparent',
    textStyle: { color: textSecondary, fontFamily: "system-ui, -apple-system, sans-serif", fontSize: 12 },
    title: { textStyle: { color: text, fontSize: 14, fontWeight: 600 }, subtextStyle: { color: textMuted, fontSize: 11 } },
    legend: { textStyle: { color: textSecondary, fontSize: 11 }, itemWidth: 18, itemHeight: 6 },
    tooltip: {
      backgroundColor: bg, borderColor: borderColor, borderWidth: 1,
      textStyle: { color: text, fontSize: 12, fontFamily: mono },
      extraCssText: 'border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); backdrop-filter: blur(8px);',
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

/** 折线图图例色标单源（path 自绘：rect 表达不了线型与阴影带）。
 *  SPA（cross-correlation.js）与专题页（rates-common.js）共用，故定义在本文件。
 *  统一 20×6 坐标系：线型占 y=2..4（渲染后 2px），阴影带填满整格（6px），两者拉开 3 倍。
 *  页面不手写 icon：reSyncLegend 按 series 线型自动挑（图例尺寸走主题默认 18×6）。 */
const RE_LEGEND = {
  solid: 'path://M0,2 L20,2 L20,4 L0,4 Z',
  dashed: 'path://M0,2 L8,2 L8,4 L0,4 Z M12,2 L20,2 L20,4 L12,4 Z',
  dotted: 'path://M0,2 L2.5,2 L2.5,4 L0,4 Z M6,2 L8.5,2 L8.5,4 L6,4 Z M12,2 L14.5,2 L14.5,4 L12,4 Z M17.5,2 L20,2 L20,4 L17.5,4 Z',
  dashdot: 'path://M0,2 L7,2 L7,4 L0,4 Z M9.5,2 L11.5,2 L11.5,4 L9.5,4 Z M14,2 L20,2 L20,4 L14,4 Z',
  band: 'path://M0,0 L20,0 L20,6 L0,6 Z',
  // 带圆点的实线（图上画 symbol: 'circle' 的那类，如曲线对比的「当前」）
  dotline: 'path://M0,2 L20,2 L20,4 L0,4 Z M7,2 A2,2 0 1,0 11,2 A2,2 0 1,0 7,2 Z',
};

// series → 图例色标：不透明阴影面积用色块（如 2s10s 利差带），否则按线型取
// 实线/虚线/点线/点划线（lineStyle.type 为数组 = 自定义 dash → 点划线）；线上有圆点则带点。
function reLegendIcon(s) {
  const a = s.areaStyle;
  if (a && a.opacity !== 0) return RE_LEGEND.band;
  const t = (s.lineStyle || {}).type;
  if (Array.isArray(t)) return RE_LEGEND.dashdot;
  const icon = RE_LEGEND[t === 'dashed' || t === 'dotted' ? t : 'solid'];
  return icon === RE_LEGEND.solid && s.symbol === 'circle' && s.showSymbol !== false ? RE_LEGEND.dotline : icon;
}

/** 图例与曲线对齐（所有 ECharts 折线图统一，页面零改动）：
 *  1) 色块颜色 = series 线色。ECharts 图例只读 series.color，不补就会落到主题调色板
 *     （第 2 条线永远显示成 palette 第 2 色）。
 *  2) 色块形状 = series 线型/阴影带；页面已显式给 icon 的项不覆盖，非 line 系列（柱/散点）不动。
 *  就地改 option 并返回它，包一层即可：chart.setOption(reSyncLegend(opt))。 */
function reSyncLegend(option) {
  const series = option.series || [];
  const byName = new Map(series.map((s) => [s.name, s]));
  series.forEach((s) => {
    if (s.type !== 'line' || s.color) return;
    const c = (s.lineStyle || {}).color || (s.itemStyle || {}).color;
    if (typeof c === 'string') s.color = c;
  });
  const lg = option.legend;
  if (!lg || lg.show === false) return option;
  const items = lg.data || series.map((s) => ({ name: s.name }));
  lg.data = items.map((it) => {
    const o = typeof it === 'string' ? { name: it } : it;
    const s = byName.get(o.name);
    return s && !o.icon && s.type === 'line' ? { ...o, icon: reLegendIcon(s) } : o;
  });
  return option;
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

/** 相关热力图发散色带（单源）：两端 = --color-brand / --color-down（语义蓝/红），
 *  中段 = --surface-alt，过渡色由端点插值生成；本文件仍无色值字面量。
 *  消费方：js/cross-correlation.js 与 assets/index.html 相关矩阵。 */
function reCorrRamp() {
  const neg = reCssVar('--color-brand'), pos = reCssVar('--color-down'), mid = reCssVar('--surface-alt');
  return [neg, mixHex(neg, mid, 0.72), mid, mixHex(pos, mid, 0.72), pos];
}
// 6 位十六进制颜色线性插值（仅供 reCorrRamp 生成过渡带）
function mixHex(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [pa, pb] = [p(a), p(b)];
  return '#' + pa.map((x, i) => Math.round(x + (pb[i] - x) * t).toString(16).padStart(2, '0')).join('');
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
