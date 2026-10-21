// app.js — entry point: tab routing + global init

// /api/ 数据请求统一 ETag 协商（与 nav.js 同款 patch；SPA 视图的 fetch
// 都从这里过），部署后浏览器不再用 max-age=600 的旧 JSON
(function () {
  if (window.__fetchNoCache) return;
  window.__fetchNoCache = true;
  const _fetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.includes('/api/')) {
      init = init || {};
      if (!init.cache) init.cache = 'no-cache';
    }
    return _fetch.call(this, input, init);
  };
})();

import { initTechView, selectSymbol as techSelectSymbol, updateStatus as techStatus } from './tech-view.js';
import { initGlobalNav, markView } from './macro-view.js';
import { initCorrelationView, updateStatus as correlationStatus } from './cross-correlation.js';

// ── 全局左侧导航 ──────────────────────────────────────────────────────────
const initialView = initGlobalNav(); // 返回 hash 里的 SPA 视图键（无则 null）

// ── dark mode ──────────────────────────────────────────────────────────────
// 主题恒跟随系统（手动切换按钮已删）；localStorage 只为「被外部 iframe 内嵌」保留
// —— 专题页的 embedded 分支读它（站内已改整页跳转，主题走 prefers-color-scheme）。
const DARK_KEY = 'ticker-toolkit-dark';
const prefersDarkMedia = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme(dark) {
  document.body.classList.toggle('dark', dark);
  localStorage.setItem(DARK_KEY, dark ? '1' : '0');
  window.dispatchEvent(new CustomEvent('theme-changed', { detail: { dark } }));
}
applyTheme(prefersDarkMedia.matches);
// 系统主题变化实时跟随
prefersDarkMedia.addEventListener('change', () => applyTheme(prefersDarkMedia.matches));

// ── shared state ───────────────────────────────────────────────────────────
export const state = {
  currentTab: 'tech',
  symbols: null,
};

const inited = {};

const views = {
  tech:         { label: '技术', init: initTechView,       status: techStatus },
  correlation:  { label: '关联', init: initCorrelationView, status: correlationStatus },
};

function switchTab(tab) {
  state.currentTab = tab;
  document.querySelector('.app').dataset.view = tab;
  markView(tab); // 侧栏高亮单入口（#24：专题页已改为整页跳转，SPA 只剩技术/关联两个工具视图）

  const view = views[tab];
  if (!inited[tab]) {
    if (view.init) view.init();
    inited[tab] = true;
  }
  if (view.status) view.status();
  else {
    document.getElementById('status-symbol').textContent = view.label || '—';
    document.getElementById('status-range').textContent = '';
    document.getElementById('status-count').textContent = '';
  }
  document.getElementById('status-refresh').textContent = '页面刷新 ' + new Date().toLocaleTimeString('zh-CN');
}

// Cross-module tab switching (watchlist clicks, 左侧导航)
window.addEventListener('switch-tab', e => switchTab(e.detail));

// ponytail: go-stock now redirects to tech view with symbol pre-selected
window.addEventListener('go-stock', e => {
  switchTab('tech');
  if (e.detail) techSelectSymbol(e.detail);
});

switchTab(initialView || 'tech');  // 默认落地 = 技术分析（ADR-0005：仪表盘已删，首页是 /daily/ 整页）
