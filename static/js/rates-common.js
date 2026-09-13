// rates-common.js — rates 子站共享工具（主题 / 图表 / 请求 / 表格 / 格式化）
// 页面加载方式：<script src="/js/rates-common.js"></script> + <script> 内使用

const R = {
  isDark: () => document.body.classList.contains('dark'),

  // ── 格式化（各页共用，避免逐页重复定义）──
  fmtPct: (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(2)}%`),
  fmtBp: (v) => (v === null || v === undefined ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}`),
  fmtB: (v) => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(0)}B`),
  // 生成器类型 → 界面文案（避免内部枚举 rules/llm 泄漏到页面，见审计 D2）
  genText: (g) => (g === 'llm' ? 'LLM' : '规则引擎（LLM 预留）'),

  // ── 时效标签（re-as-of）文案唯一组装处 ──
  // AGENTS 规范：前缀固定「数据截至」，多源用 ` · ` 分段，日期一律 ISO，月频区间写「月频 起–止」。
  //   R.asOf(date)                        → 「数据截至 2026-09-04」
  //   R.asOf([['TIC', d1], ['拍卖', d2]])   → 「数据截至 TIC d1 · 拍卖 d2」
  //   R.asOf([R.asMonth([a, b]), ['盈亏平衡', c]]) → 「数据截至 月频 a–b · 盈亏平衡 c」
  // 段值为空（null / '' / false / [label, null]）自动丢弃，全空返回 ''（页面不显示前缀）。
  asOf(src) {
    const list = typeof src === 'string' ? [src] : src || [];
    const segs = list
      .map((s) => (Array.isArray(s) ? (s[1] ? `${s[0]} ${s[1]}` : '') : s || ''))
      .filter(Boolean);
    return segs.length ? `数据截至 ${segs.join(' · ')}` : '';
  },
  // 月频发布滞后：取一组观测日的最早–最晚，拼成「月频 起–止」时效段（配合 asOf 用）
  asMonth: (dates) => {
    const d = (dates || []).filter(Boolean).sort();
    return d.length ? `月频 ${d[0]}–${d[d.length - 1]}` : '';
  },
  ts: (arr) => (arr || []).map(p => p.date),
  vs: (arr) => (arr || []).map(p => p.value),

  // 请求失败时向多个容器注入错误提示
  fail(ids, e) {
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="re-error">加载失败：${e.message}<br>请确认服务已启动（uv run python -m src.server）或数据已拉取，刷新重试</div>`;
    });
  },

  // ECharts 配色：全部回读 tokens.css / app.css 的 CSS 变量（单一来源，禁止字面色值）。
  // 键名保持不变，各页 optionFn 调用点不动；主题切换时重新调用即拿到新主题值。
  colors() {
    const v = (n) => getComputedStyle(document.body).getPropertyValue(n).trim();
    return {
      text: v('--text-secondary'),
      muted: v('--text-dim'),
      border: v('--border'),
      grid: v('--border-light'),
      bg: v('--surface'),
      blue: v('--color-brand'), orange: v('--color-warn'), green: v('--color-up'),
      red: v('--color-down'), gray: v('--color-neutral'), purple: v('--chart-purple'),
    };
  },

  // 时间轴标签简写 M/D（各页 time 轴 axisLabel.formatter 共用，免逐页重写）
  md: (v) => { const t = new Date(v); return `${t.getMonth() + 1}/${t.getDate()}`; },

  // 图表默认项骨架（顶层浅合并；legend/grid 深一层）：统一 legend 样板与 grid 边距，
  // 页面只传差异部分：R.mkChart(id, (colors) => R.lineOption({ legend: { data: [...] }, series: [...] }, colors))
  lineOption(over, colors) {
    const c = colors || R.colors();
    const base = {
      legend: { top: 4, right: 8, icon: 'rect', itemWidth: 12, itemHeight: 3, textStyle: { color: c.text, fontSize: 11 } },
      grid: { top: 36, left: 48, right: 16, bottom: 24 },
    };
    const out = Object.assign({}, base, over);
    if (over && over.legend) out.legend = Object.assign({}, base.legend, over.legend);
    if (over && over.grid) out.grid = Object.assign({}, base.grid, over.grid);
    return out;
  },

  // 建图 + 注册主题联动（Cmd+T 时自动重渲染）：按主题名 init（未显式配色的 series
  // 走主题 color 数组兜底，不再漏出 ECharts 默认紫）；切换时重注册 + dispose 重建
  // （ECharts 主题只在 init 生效，同实例 setOption 换不掉主题默认值）。
  // 重建后页面持有的旧引用会失效，故按 id 登记当前实例，页面用 R.getChart(id) 取最新。
  mkChart(id, option) {
    const dom = document.getElementById(id);
    if (!dom) return null;
    const render = () => {
      if (window.registerMacroTheme) registerMacroTheme();
      const chart = echarts.init(dom, R.isDark() ? 'macroDark' : 'macro');
      chart.setOption(option(R.colors()));
      R._charts.set(id, chart);
      return chart;
    };
    let chart = render();
    window.addEventListener('theme-changed', () => {
      try { chart.dispose(); } catch (e) { /* 已被外部 dispose */ }
      chart = render();
    });
    new ResizeObserver(() => chart.resize()).observe(dom);
    return chart;
  },

  // id → 当前实例（主题重建后仍有效）；外部 dispose 过的返回 null
  _charts: new Map(),
  getChart(id) {
    const c = R._charts.get(id);
    return c && !c.isDisposed() ? c : null;
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
    const numCol = new Array(headers.length).fill(false);  // 含真实数值的列，整列右对齐（含 — 占位）
    const pending = [];  // [td, col, isNum] 先收集后统一应用，避免 — 与数值混排时锯齿
    rows.forEach(row => {
      const r = document.createElement('tr');
      headers.forEach((h, j) => {
        const td = document.createElement('td');
        const key = (keys && keys[j]) || Object.keys(row)[j];
        const fmt = formatters[key] || ((v) => (v === null || v === undefined || v === '' ? '—' : String(v)));
        const cell = fmt(row[key], row);
        if (html) td.innerHTML = cell; else td.textContent = cell;
        // 数值列右对齐（纯数字/百分号/负号/单位符号开头），小数位纵向对齐；首列名称保持左对齐。
        // html 模式下先剥标签再测（如 <span>-0.15</span>）；单位前允许空格（如 "+0.5 pct"）
        const plain = html ? String(cell).replace(/<[^>]+>/g, '').trim() : String(cell).trim();
        const isNum = j > 0 && /^[-+—]?[$€¥]?[\d.,]+\s?(%|x|bp|pct|pp|k|m|b|t)?$/i.test(plain) && plain !== '—';
        pending.push([td, j, isNum]);
        if (isNum) numCol[j] = true;
        r.appendChild(td);
      });
      tbody.appendChild(r);
    });
    table.appendChild(tbody);
    // 统一应用：数值列的所有单元格（含 — 占位）与表头都右对齐，保证列内成线
    pending.forEach(([td, j, isNum]) => { if (isNum || numCol[j]) td.style.textAlign = 'right'; });
    numCol.forEach((isNum, j) => { if (isNum) thead.rows[0].cells[j].style.textAlign = 'right'; });
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
      // 长值（如“3.50% – 3.75%”、“2026-09-16”）按卡片宽度自动缩字号，防溢出；
      // 仅当卡片宽度真正变化时重算（守卫 lastW，避免改字号→高度变→循环触发）
      const v = d.querySelector('.re-corridor-value');
      let lastW = 0;
      const fit = () => {
        const w = d.clientWidth;
        if (w === lastW) return;
        lastW = w;
        v.style.fontSize = '';
        const avail = w - 40; // 卡片 padding 36px + 4px 安全边距（非整数字号下字形取整会多出几 px）
        if (avail > 0 && v.scrollWidth > avail) {
          v.style.fontSize = Math.max(12, Math.floor((220 * avail) / v.scrollWidth) / 10) + 'px';
        }
      };
      row.appendChild(d);
      new ResizeObserver(fit).observe(d);
    });
    return row;
  },
};

// 主题由 nav.js 统一管理（localStorage 'ticker-toolkit-dark'，无偏好时跟随系统）；
// 本文件只需监听 theme-changed 重绘图表（见上方监听器）
