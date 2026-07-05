// app.js — entry point: tab routing + global init

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
  dashboard: { label: '仪表盘' },
  tech:      { label: '技术',  init: initTechView,  status: techStatus },
  macro:     { label: '宏观',  init: initMacroView, status: macroStatus },
  correlation: { label: '关联', init: initCorrelationView, status: correlationStatus },
  liquidity:   { label: '流动性', init: initLiquidityView, status: liquidityStatus },
  stock:       { label: '个股', init: initStockView, status: stockStatus },
};

function switchTab(tab) {
  // Cleanup previous view if needed
  if (state.currentTab === 'stock' && tab !== 'stock') {
    stockCleanup();
    inited['stock'] = false;  // allow re-init on next visit
  }
  state.currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelector('.app').dataset.view = tab;

  const view = views[tab];
  if (view.init && !inited[tab]) {
    view.init();
    inited[tab] = true;
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

switchTab('dashboard');
