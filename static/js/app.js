// app.js — entry point: tab routing + global init

import { initDashboard, refresh as dashRefresh, cleanup as dashCleanup, updateStatus as dashStatus } from './dashboard.js';
import { initTechView, updateStatus as techStatus } from './tech-view.js';
import { initMacroView, updateStatus as macroStatus } from './macro-view.js';
import { initCorrelationView, updateStatus as correlationStatus } from './cross-correlation.js';
import { initLiquidityView, updateStatus as liquidityStatus } from './liquidity-heatmap.js';
import { initStockView, updateStatus as stockStatus, cleanup as stockCleanup } from './stock-kline.js';

// ponytail: shared state — only what cross-module consumers need
export const state = {
  currentTab: 'dashboard',
  symbols: null,
};

const inited = {};

const views = {
  dashboard:    { label: '仪表盘', init: initDashboard,      status: dashStatus,        cleanup: dashCleanup, refresh: dashRefresh },
  tech:         { label: '技术',   init: initTechView,       status: techStatus },
  macro:        { label: '宏观',   init: initMacroView,      status: macroStatus },
  correlation:  { label: '关联',   init: initCorrelationView, status: correlationStatus },
  liquidity:    { label: '流动性', init: initLiquidityView,   status: liquidityStatus },
  stock:        { label: '个股',   init: initStockView,      status: stockStatus, cleanup: stockCleanup },
};

function switchTab(tab) {
  const prev = state.currentTab;

  // Cleanup previous view if needed
  if (prev === 'stock' && tab !== 'stock') {
    stockCleanup();
    inited['stock'] = false;
  }
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
}

document.querySelectorAll('.tab-btn').forEach(btn =>
  btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

// Cross-module tab switching (dashboard mini-charts, watchlist clicks)
window.addEventListener('switch-tab', e => switchTab(e.detail));

// Auto-select stock when jumping from dashboard watchlist
window.addEventListener('go-stock', e => {
  window._pendingStockSymbol = e.detail;
});

switchTab('dashboard');
