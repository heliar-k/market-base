// app.js — entry point: tab routing + global init

import { initDashboard, refresh as dashRefresh, cleanup as dashCleanup, updateStatus as dashStatus } from './dashboard.js';
import { initTechView, selectSymbol as techSelectSymbol, updateStatus as techStatus } from './tech-view.js';
import { initMacroView, updateStatus as macroStatus } from './macro-view.js';
import { initCorrelationView, updateStatus as correlationStatus } from './cross-correlation.js';

// ── dark mode ──────────────────────────────────────────────────────────────
const DARK_KEY = 'ticker-toolkit-dark';
const prefersDarkMedia = window.matchMedia('(prefers-color-scheme: dark)');
let manual = false; // 会话级手动覆盖：点按钮后停止跟随系统，刷新恢复自动
function applyTheme(dark) {
  document.body.classList.toggle('dark', dark);
  document.getElementById('theme-toggle').textContent = dark ? '☀️' : '🌙';
  localStorage.setItem(DARK_KEY, dark ? '1' : '0'); // 同步给内嵌专题 iframe（storage 事件）
  window.dispatchEvent(new CustomEvent('theme-changed', { detail: { dark } }));
}
applyTheme(prefersDarkMedia.matches);
// 系统主题变化：未手动切换过则实时跟随
prefersDarkMedia.addEventListener('change', () => {
  if (!manual) applyTheme(prefersDarkMedia.matches);
});
document.getElementById('theme-toggle').addEventListener('click', () => {
  manual = true;
  applyTheme(!document.body.classList.contains('dark'));
});

// ── shared state ───────────────────────────────────────────────────────────
export const state = {
  currentTab: 'macro',
  symbols: null,
};

const inited = {};

const views = {
  dashboard:    { label: '仪表盘', init: initDashboard,      status: dashStatus,        cleanup: dashCleanup, refresh: dashRefresh },
  tech:         { label: '技术',   init: initTechView,       status: techStatus },
  macro:        { label: '宏观',   init: initMacroView,      status: macroStatus },
  correlation:  { label: '关联',   init: initCorrelationView, status: correlationStatus },
};

function switchTab(tab) {
  const prev = state.currentTab;

  // Cleanup previous view if needed
  if (prev === 'dashboard' && tab !== 'dashboard') {
    dashCleanup();
    inited['dashboard'] = false;
  }

  state.currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelector('.app').dataset.view = tab;

  const view = views[tab];
  if (!inited[tab]) {
    if (view.init) view.init();
    inited[tab] = true;
  } else if (view.refresh) {
    // ponytail: refresh on re-visit (dashboard re-loads data)
    view.refresh();
  }
  if (view.status) view.status();
  else {
    document.getElementById('status-symbol').textContent = view.label || '—';
    document.getElementById('status-range').textContent = '';
    document.getElementById('status-count').textContent = '';
  }
  document.getElementById('status-refresh').textContent = '更新: ' + new Date().toLocaleTimeString('zh-CN');
}

document.querySelectorAll('.tab-btn').forEach(btn =>
  btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

// Cross-module tab switching (dashboard mini-charts, watchlist clicks)
window.addEventListener('switch-tab', e => switchTab(e.detail));

// ponytail: go-stock now redirects to tech view with symbol pre-selected
window.addEventListener('go-stock', e => {
  switchTab('tech');
  if (e.detail) techSelectSymbol(e.detail);
});

switchTab('macro');  // 默认进入宏观页（仪表盘已隐藏）
