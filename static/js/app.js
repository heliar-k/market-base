// app.js — entry point: tab routing + global init

import { initTechView, updateStatus as techStatus } from './tech-view.js';
import { initMacroView, updateStatus as macroStatus } from './macro-view.js';

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
  correlation: { label: '关联' },
  liquidity:   { label: '流动性' },
  stock:       { label: '个股' },
};

function switchTab(tab) {
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
