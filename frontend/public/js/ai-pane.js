// ai-pane.js — 伴读抽屉「桌面分栏」的宽度与形态纯逻辑（不碰 DOM，可直接用 node 测）。
//
// 分栏形态：宽屏打开抽屉时，正文让出 --ai-w 的宽度（body 的 padding-right），抽屉本身
// 仍 fixed 在右边缘。这样不挪 DOM、不动滚动容器 —— 专题页（普通文档流 + body.padding-left）
// 和 SPA（100dvh flex 壳 + 内部 .center 滚动）两种 body 都吃同一条规则，34 个专题页零改动。
//
// 三个数都是量出来的，不是拍的：
//   - NAV_W 240        左侧专题树（body.re-has-side 的 padding-left / SPA .app 的 margin-left）
//   - CONTENT_FLOOR 700 正文内宽地板。1440 屏开 380 实测：正文 1136→756、4 卡行 275→180、
//                      宽表开始横滚（yield-curve 788 > 738）；再压就是「1024 窗口那档」，
//                      所以拖拽上限由它反推，而不是给个「不超过 50vw」的拍脑袋值
//   - PANE_MIN 320      抽屉头 6 个按钮实测占 275px，宽 300 时 scrollWidth 307 溢出，320 刚好
export const PANE_MIN = 320;
export const PANE_MAX = 640;
export const PANE_DEFAULT = 380;
export const NAV_W = 240;
export const CONTENT_FLOOR = 700;
export const ROOT_PAD = 64; // .re-root 两侧 padding（32×2）
export const NARROW_MAX = 768; // 与 app.css 的 .ai-drawer 整屏档同一个断点

/** 分栏可用（= 正文地板 + 最小抽屉都放得下）的最小视口宽度。 */
export const PANE_BREAKPOINT = NAV_W + CONTENT_FLOOR + ROOT_PAD + PANE_MIN; // 1324

/** 媒体查询串：ai-chat.js 用它跟着断点变（别在 DOM 侧再写一遍 1324）。 */
export const PANE_MQ = `(min-width: ${PANE_BREAKPOINT}px)`;

/** 该视口下正文地板允许的最大抽屉宽度（可能小于 PANE_MIN，调用方在非分栏态不考虑）。 */
export function paneMaxWidth(viewport, floor = CONTENT_FLOOR) {
  const vw = Number(viewport) || 0;
  return Math.max(0, Math.min(PANE_MAX, vw - NAV_W - floor - ROOT_PAD));
}

/** 把任意宽度夹进当前视口的可用区间：非数字回落默认宽度，越界（含拖过头的负数）夹到两端。 */
export function clampPaneWidth(w, viewport, floor = CONTENT_FLOOR) {
  const n = Number(w);
  // 注意不能把「负数」当脏值：往右拖过下限时算出来就是负的，落回默认宽会变成
  // 「拖过 320 反而弹回 380」的假动作（实测踩过）。
  const base = Number.isFinite(n) ? n : PANE_DEFAULT;
  const max = Math.max(PANE_MIN, paneMaxWidth(viewport, floor));
  return Math.round(Math.min(Math.max(base, PANE_MIN), max));
}

/** 打开抽屉时的形态：'full'（≤768 整屏）/ 'pane'（宽屏分栏）/ 'overlay'（浮层）。 */
export function paneMode(viewport, wantPane = true) {
  const vw = Number(viewport) || 0;
  if (vw <= NARROW_MAX) return 'full';
  if (!wantPane) return 'overlay';
  return vw >= PANE_BREAKPOINT ? 'pane' : 'overlay';
}

/** 只有分栏态才让正文让位；浮层/整屏都不动布局。 */
export const panePushes = (viewport, wantPane = true) => paneMode(viewport, wantPane) === 'pane';
