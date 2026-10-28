// watchlist.js — 自选清单单源（ADR-0005 T3）：localStorage 键 + 增删 + 标的校验。
//
// 消费方：
//   tech-view.js → 技术分析侧栏「自选」分组（家，ADR-0005）
//   dashboard.js → 仪表盘自选卡（过渡期并存，T4 删视图后本模块仅剩侧栏一个消费方）
//
// 渲染不进本模块：两侧行结构不同（sidebar-row vs dash-watch-row），只共享状态与行为，
// 避免两份 localStorage 读写/校验逻辑平行演化。

export const WATCH_KEY = 'guanlan-watchlist';
export const DEFAULT_WATCH = ['SPX', 'NVDA'];

export function loadWatch() {
  try {
    const list = JSON.parse(localStorage.getItem(WATCH_KEY));
    return Array.isArray(list) ? list : [...DEFAULT_WATCH];
  } catch {
    return [...DEFAULT_WATCH];
  }
}

export function saveWatch(list) {
  localStorage.setItem(WATCH_KEY, JSON.stringify(list));
}

export function removeWatch(sym) {
  saveWatch(loadWatch().filter((s) => s !== sym));
}

// 全部可选标的名（/api/symbols 的 name 字段，兼容纯字符串数组）
export async function fetchSymbolNames() {
  const res = await fetch('/api/symbols');
  const arr = await res.json();
  return arr.map((s) => s.name ?? s);
}

// 自选清单校验：只留 /api/symbols 里存在的标的（数据集变更时自动清理失效项）
export async function validWatch() {
  const list = loadWatch();
  try {
    const all = new Set(await fetchSymbolNames());
    return list.filter((s) => all.has(s));
  } catch {
    return list;
  }
}
