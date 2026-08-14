// rates-common.js — rates 子站共享工具（主题 / 图表 / 请求 / 表格 / 格式化）
// 页面加载方式：<script src="/js/rates-common.js"></script> + <script> 内使用

const R = {
  isDark: () => document.body.classList.contains('dark'),

  // ── 格式化（各页共用，避免逐页重复定义）──
  fmtPct: (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(2)}%`),
  fmtBp: (v) => (v === null || v === undefined ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}`),
  fmtB: (v) => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(0)}B`),
  ts: (arr) => (arr || []).map(p => p.date),
  vs: (arr) => (arr || []).map(p => p.value),

  // 请求失败时向多个容器注入错误提示
  fail(ids, e) {
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="re-error">加载失败: ${e.message}</div>`;
    });
  },

  // ECharts 主题色（跟随 Cmd+T 切换）
  colors() {
    const dark = R.isDark();
    return {
      text: dark ? '#8b949e' : '#666',
      muted: dark ? '#484f58' : '#999',
      border: dark ? '#30363d' : '#e1e4e8',
      grid: dark ? '#2d333b' : '#f0f0f0',
      bg: dark ? '#161b22' : '#fff',
      blue: '#3b82f6', orange: '#ff9800', green: '#26a69a',
      red: '#ef5350', gray: '#9ca3af', purple: '#a78bfa',
    };
  },

  // 建图 + 注册主题联动（Cmd+T 时自动重渲染）
  mkChart(id, option) {
    const dom = document.getElementById(id);
    if (!dom) return null;
    const chart = echarts.init(dom);
    chart.setOption(option(R.colors()));
    window.addEventListener('theme-changed', () => {
      chart.setOption(option(R.colors()));
    });
    new ResizeObserver(() => chart.resize()).observe(dom);
    return chart;
  },

  // 请求 + 错误处理
  async get(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  },

  // 数据表格（复用 expectations 页的 re-table 样式）
  // keys: 可选，显式指定每列对应的行键；缺省按 Object.keys(row) 顺序取列
  // （行键顺序与列头不一致时会错列，显式 keys 可避免——审计 P1-③）
  // html: true 时单元格用 innerHTML（允许 formatter 返回带 <span> 的着色文本）
  table(headers, rows, formatters = {}, keys = null, html = false) {
    const wrap = document.createElement('div');
    wrap.className = 're-table-wrap';
    const table = document.createElement('table');
    table.className = 're-table';
    const thead = document.createElement('thead');
    const tr = document.createElement('tr');
    headers.forEach(h => { const th = document.createElement('th'); th.textContent = h; tr.appendChild(th); });
    thead.appendChild(tr);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    rows.forEach(row => {
      const r = document.createElement('tr');
      headers.forEach((h, j) => {
        const td = document.createElement('td');
        const key = (keys && keys[j]) || Object.keys(row)[j];
        const fmt = formatters[key] || ((v) => (v === null || v === undefined || v === '' ? '—' : String(v)));
        const cell = fmt(row[key], row);
        if (html) td.innerHTML = cell; else td.textContent = cell;
        r.appendChild(td);
      });
      tbody.appendChild(r);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  },

  // 数值卡片行
  cards(items) {
    const row = document.createElement('div');
    row.className = 're-cards';
    items.forEach(({ label, value, sub, accent }) => {
      const d = document.createElement('div');
      d.className = 're-card' + (accent ? ' re-card-accent' : '');
      d.innerHTML = `<div class="re-corridor-label">${label}</div>
        <div class="re-corridor-value">${value}</div>
        <div class="re-card-sub">${sub || ''}</div>`;
      row.appendChild(d);
    });
    return row;
  },
};

// 主题由 nav.js 统一管理（localStorage 'ticker-toolkit-dark'，无偏好时跟随系统）；
// 本文件只需监听 theme-changed 重绘图表（见上方监听器）
