// rates-common.js — rates 子站共享工具（主题 / 图表 / 请求 / 表格 / 格式化）
// 页面加载方式：<script src="/js/rates-common.js"></script> + <script> 内使用

const R = {
  isDark: () => document.body.classList.contains('dark'),

  // ── 格式化（各页共用，避免逐页重复定义）──
  fmtPct: (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(2)}%`),
  fmtBp: (v) => (v === null || v === undefined ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}`),
  fmtB: (v) => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(0)}B`),
  // 千分位数值：取代各页自写的 fmt（bonds/etfs/crypto 默认 2 位、commodities/fx 要 4 位、
  // crypto-derivatives 透传 d）。v === 0 ? 0 : v 是 -0 守卫，保留原行为。
  num: (v, d = 2) => (v == null ? '—' : Number(v === 0 ? 0 : v).toLocaleString(undefined, { maximumFractionDigits: d })),
  // 涨跌文本 span：▲ +x.xx% / ▼ x.xx%，颜色走文字档（亮色过 AA，图形档会跌到 3.6:1）。
  // 以前在 bonds/commodities/crypto 各拄一份，改语义色得同改三处。
  chgSpan: (v) => (v == null ? '—' : `<span style="color:${v >= 0 ? 'var(--up)' : 'var(--down)'}">${v >= 0 ? '▲ +' : '▼ '}${Number(v).toFixed(2)}%</span>`),
  // 生成器类型 → 界面文案（避免内部枚举 rules/llm 泄漏到页面，见审计 D2）
  genText: (g) => (g === 'llm' ? 'LLM' : '规则引擎（LLM 预留）'),

  // ── 跨资产表数值格式化单源（unit 来自 /api/daily-brief 行；daily 页与仪表盘共用）──
  // bn 的单位是「百万」：≥1e6 M → T，≥1e3 M → B，否则 M。
  fmtLast(unit, v) {
    if (v == null) return '—';
    if (unit === 'bn') return v === 0 ? '$0'
      : v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'T'
      : v >= 1e3 ? '$' + Math.round(v / 1e3 * 10) / 10 + 'B'
      : '$' + Math.round(v).toLocaleString('en-US') + 'M';
    if (unit === 'pct_bp') return Number(v).toFixed(2) + '%';
    if (unit === 'bp') return Number(v).toFixed(1) + 'bp';
    if (unit === 'pt') return Number(v).toFixed(2);
    return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
  },
  // 涨跌 chip：符号一律在最前（`$-2B` 读起来像「负美元」），且显示值四舍五入到 0 就不着色
  // （RRP Δ=-0.4M 曾渲染成红色的 "$0M"——有颜色没数值比没数值更误导）。
  fmtChg(unit, v) {
    if (v == null) return '<span style="color:var(--text-dim)">—</span>';
    const a = Math.abs(v);
    const body = unit === 'bp' || unit === 'pct_bp' ? a.toFixed(1) + 'bp'
      : unit === 'pt' ? a.toFixed(2) + 'pt'
      : unit === 'bn' ? (a >= 1e6 ? '$' + (a / 1e6).toFixed(2) + 'T'
        : a >= 1e3 ? '$' + Math.round(a / 1e3) + 'B' : '$' + Math.round(a) + 'M')
      : a.toFixed(2) + '%';
    if (!/[1-9]/.test(body)) return `<span style="color:var(--text-dim)">${body}</span>`;
    return `<span class="${v > 0 ? 'up' : 'down'}">${v > 0 ? '+' : '-'}${body}</span>`;
  },

  // ── 研判文案 → .sig-block（全站研判段共用渲染：rates 四段 + 三段式页）──
  // 引擎侧契约：body 用 \n 分行（结论 / 依据 / 触发）。
  //   labels 给了→按行取标签（rates：结论/依据/触发，卡面自带 h2 标题）；
  //   没给→整段用 title 一个标题（现状/结构/展望 一类语义标题）。
  // 弱化规则：条件句才走 .note。labels 模式末行固定是「触发」→ 弱化；
  // 无标签模式两行 = 判断 + 读数（读数是事实，不能比判断还弱）→ 只有 ≥3 行
  // （判断/依据/触发）才弱化末行。单行 = 降级文案（不挂标签）。
  // 文本一律 R.esc 转义（页面不造平行 helper）。
  sigBlocks: (title, body, labels = null) => {
    const lines = String(body ?? '').split('\n').filter(s => s.trim());
    if (!lines.length) return '';
    const dimTail = labels ? lines.length > 1 : lines.length >= 3;
    const note = (j) => (dimTail && j === lines.length - 1 ? ' class="note"' : '');
    if (labels) {
      if (lines.length === 1) return `<div class="sig-block"><p>${R.esc(lines[0])}</p></div>`;
      return lines.map((t, j) =>
        `<div class="sig-block"><div class="sig-title">${R.esc(labels[j] || labels[labels.length - 1])}</div>`
        + `<p${note(j)}>${R.esc(t)}</p></div>`).join('');
    }
    return `<div class="sig-block"><div class="sig-title">${R.esc(title)}</div>`
      + lines.map((t, j) => `<p${note(j)}>${R.esc(t)}</p>`).join('') + '</div>';
  },

  // ── 研判行契约标签（全站单源，页面不再各写一份 LINE_TITLES）──
  // 引擎两种契约：三段式「结论 / 依据 / 触发」(rates / liquidity / volatility / labor…)
  // 与两段式「判断 / 读数」(treasury / inflation / assets…)。行数为 1 时 R.sigBlocks
  // 自动不挂标题（降级文案不硬套标签）。
  LINES_CONCLUSION: ['结论', '依据', '触发'],
  LINES_JUDGE: ['判断', '读数'],

  // ── 研判段 → 一张 .re-section 外卡（段标题在左上 + 内嵌 .sig-row 横排小卡）──
  // 承载波动率页定下来的形状，全站研判（treasury/inflation/labor/credit/rates/…）统一走这里；
  // 页面不再手写 re-section + sigBlocks 拼装，改形状只改这一处。
  //   title   段标题（外卡 h2，左上）
  //   body    \n 分行的段文本
  //   labels 按行取的小卡标题；不给 = 整段一张小卡、标题用 title
  judgeCard(title, body, labels = null) {
    // 单行段没有「行」可拆：不套内卡（sig-title 会与外卡 h2 重复），直接一张外卡一段文
    const lines = String(body ?? '').split('\n').filter(s => s.trim());
    if (lines.length <= 1) {
      const one = lines[0] ?? '';
      return one ? `<div class="re-section"><h2>${R.esc(title)}</h2><p>${R.esc(one)}</p></div>` : '';
    }
    const blocks = R.sigBlocks(title, body, labels);
    if (!blocks) return '';      // 空文本不渲染空卡
    return `<div class="re-section"><h2>${R.esc(title)}</h2><div class="sig-row">${blocks}</div></div>`;
  },

  // ── 小节标题单源：中文为主，英文只作弱化注解 ──
  // 形状：中文标题 + 右侧 12px 弱化英文（窄屏自动换行到标题下方），杜绝「CORRELATION 跨资产相关性」
  // 这类中英堆叠标题。en 与 note 都给时拼成「NOW · 8 个核心 KPI…」。
  // JS 生成的 .re-section 卡用这个；静态层走 <Section title=… sub=…>（sub 同一套 .re-sec-sub）。
  secTitle: (zh, en = '', note = '') => {
    const tail = [en, note].filter(Boolean).join(' · ');
    return `<h2>${R.esc(zh)}${tail ? ` <span class="re-sec-sub">${R.esc(tail)}</span>` : ''}</h2>`;
  },

  // ── 页脚「本页」行（<slot name="foot"> → #re-foot）文案单源 ──
  // 页脚只剩这一行来源说明（全站级枚举已删），所以不挂「本页」前缀。
  // 顺序固定：研判生成 → 数据源 → 口径；缺段自动丢弃，全空 → 整行不占位（special.css :empty）。
  // 术语只用「数据源」（旧的同义叫法已废弃，守卫见 test_page_level_source_term_is_data_source）。
  // 时间不写这里 —— 页头 #re-as-of 已由 R.asOf 给过，同页不重复。
  // gen 传 analysis 的 generator 字段（rules/llm）；无研判段就置空（不假称规则引擎）。
  footText: ({ gen = null, src = '', note = '' } = {}) =>
    [
      gen ? `研判生成：${R.genText(gen)}` : '',
      src ? `数据源：${src}` : '',
      note ? `口径：${note}` : '',
    ]
      .filter(Boolean)
      .join(' · '),

  // 组装 + 写入一步到位（页面不重复 #re-foot 这个 id）；容器不存在（404 页）静默返回。
  // src / note 只给内容，前缀「数据源：」「口径：」由本函数加 —— 术语不在页面各自重打一遍。
  foot(gen, opts = {}) {
    const el = document.getElementById('re-foot');
    if (!el) return;
    const { src = '', note = '' } = opts;
    el.textContent = R.footText({ gen, src, note });
  },

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

  // 四线曲线快照（当前 / 1周前 / 1月前 / 3月前，rates 入口页 + 收益率曲线页共用）
  curveCompare(id, tenors) {
    return R.mkChart(id, (colors) => R.lineOption({
      legend: { data: ['当前', '1周前', '1月前', '3月前'] },
      tooltip: { trigger: 'axis', valueFormatter: v => `${Number(v).toFixed(2)}%` },
      xAxis: { type: 'category', data: tenors.map(t => t.tenor), axisLabel: { color: colors.muted, fontSize: 10 }, axisLine: { lineStyle: { color: colors.border } } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: colors.muted, formatter: '{value}%' }, splitLine: { lineStyle: { color: colors.grid } } },
      series: [
        { name: '当前', type: 'line', data: tenors.map(t => t.current), symbol: 'circle', symbolSize: 6, lineStyle: { color: colors.blue, width: 2 }, itemStyle: { color: colors.blue }, connectNulls: true },
        { name: '1周前', type: 'line', data: tenors.map(t => t.prev_1w), symbol: 'none', lineStyle: { color: colors.gray, type: 'dashed', width: 1.5 }, connectNulls: true },
        { name: '1月前', type: 'line', data: tenors.map(t => t.prev_1m), symbol: 'none', lineStyle: { color: colors.orange, type: 'dashed', width: 1.5 }, connectNulls: true },
        { name: '3月前', type: 'line', data: tenors.map(t => t.prev_3m), symbol: 'none', lineStyle: { color: colors.red, type: 'dashed', width: 1.5 }, connectNulls: true },
      ],
    }, colors));
  },

  // 请求失败时向多个容器注入错误提示（线上为静态导出，本地为 server）
  // 段级失败占位。hint 缺省时只给「不指名命令」的通用建议 —— 写死 ./bin/fetch_fred
  // 对非 FRED 页是错的（美债页会被告知去跑一个与本页无关的脚本）。
  fail(ids, e, hint = "线上请确认对应数据源已更新后重新部署") {
    ids.forEach(id => {
      const el = document.getElementById(id);
      // 与 setSection 同法：报错占位也不该把小节标题一并清掉
      if (el) {
        el.querySelectorAll(':scope > :not(header)').forEach((n) => n.remove());
        el.insertAdjacentHTML('beforeend', `<div class="re-error">加载失败：${e.message}<br>本地请确认 uv run python -m src.server 已启动；${hint}</div>`);
      }
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
      // 文字档（ECharts label / axisLabel / markLine 标注等**图上写字**用这三个）：
      // green/red/orange 是图形档，亮色主题下做文字只有 3.6:1 不达 AA（tokens.css 已注明
      // 「-text 档只给 color: 用」）；对比度回归测试扫不到 JS 对象键，故单源在这里给齐。
      greenText: v('--color-up-text'), redText: v('--color-down-text'),
      orangeText: v('--color-warn-text'),
    };
  },

  // 用户本地今天（ISO YYYY-MM-DD，可传偏移天数）：展示给用户的时间节点基准
  // （倒计时/窗口过滤）。不用数据 as_of（可能滞后），也不用 toISOString()（UTC 在西区时区差一天）
  todayISO: (offsetDays = 0) => {
    const t = new Date(Date.now() + offsetDays * 864e5);
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
  },

  // 下一场未结束会议（end_day >= 今天）：静态 JSON 冻结了构建期日历，按打开时间从
  // meetings 重选；旧 JSON 无 meetings 时回退 next。注意 end_day 是日号（int），
  // 必须先拼成完整 ISO 串再与 todayISO 比较（int 对日期串是 NaN 比较恒 false）
  nextMeeting: (fomc) => {
    const list = fomc?.meetings || (fomc?.next ? [fomc.next] : []);
    const today = R.todayISO();
    const isoEnd = (m) => `${m.year}-${String(m.month).padStart(2, '0')}-${String(m.end_day).padStart(2, '0')}`;
    return list.find((m) => m && isoEnd(m) >= today) || null;
  },

  // 时间轴标签简写 M/D（各页 time 轴 axisLabel.formatter 共用，免逐页重写）
  // 纯日期串按本地午夜解析（new Date("YYYY-MM-DD") 是 UTC 午夜，西区时区会错位一天）
  md: (v) => { const t = new Date(typeof v === 'string' && v.length === 10 ? v + 'T00:00:00' : v); return `${t.getMonth() + 1}/${t.getDate()}`; },

  // HTML 转义（Polymarket / SEC 等外部原文入 innerHTML 前必过）
  esc: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),

  // 迷你走势缩略图（状态卡里的均值线缩略，SVG polyline，不建 ECharts 实例）
  // pts: [{date, value}, …]；少于 2 点返回 ''（无走势可言）
  spark(pts, w = 96, h = 30) {
    if (!pts || pts.length < 2) return '';
    const vs = pts.map((p) => p.value);
    const min = Math.min(...vs), max = Math.max(...vs), span = (max - min) || 1e-9;
    const xy = (i, v) => `${(i / (pts.length - 1) * w).toFixed(1)},${(h - 3 - (v - min) / span * (h - 6)).toFixed(1)}`;
    const [lx, ly] = xy(vs.length - 1, vs[vs.length - 1]).split(',');
    return `<svg class="pm-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <polyline points="${vs.map((v, i) => xy(i, v)).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="1.5" opacity=".85"/>
      <circle cx="${lx}" cy="${ly}" r="2" fill="var(--accent)"/></svg>`;
  },

  // 价位格式化：$80K / $2.6K / $2.89（价位阶梯用，避免 5 位数挤爆窄列）
  usd: (v) => (v == null ? '' : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M`
    : v >= 1e3 ? `$${(v / 1e3).toFixed(v >= 1e5 ? 0 : 1)}K` : `$${v.toFixed(2)}`),
  // 距锚点（现价）偏离：+13.5% / -8.2%；无锚点或无价位返回 ''
  dist: (v, anchor) => (v == null || !anchor ? '' : `${(v / anchor - 1) >= 0 ? '+' : ''}${((v / anchor - 1) * 100).toFixed(1)}%`),

  // 归类 chips（预测市场面板共用：中文名 + 均概率 + 7 日变动 + 合约数 + 中位触及档）
  pmChip(c) {
    const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
    const chg = c.chg7d == null ? '' : `${c.chg7d > 0 ? '+' : ''}${c.chg7d.toFixed(1)}pp`;
    const pv = c.pivot
      ? `<span class="pv" title="归类内概率最接近 50% 的档位（市场对赌的价位）${c.pivot.dist ? ` · 距现价 ${c.pivot.dist}` : ''}">${R.usd(c.pivot.strike)}</span>`
      : '';
    return `<span class="pm-chip${c.miss ? ' miss' : ''}"${c.miss ? ' title="未命中归类规则，请补归类关键词"' : ''}>${R.esc(c.name)} <b>${pct(c.prob)}</b>` +
      `<span class="chg ${c.chg7d > 0 ? 'up' : c.chg7d < 0 ? 'down' : ''}">${chg}</span>` + pv +
      `<span class="n">${c.count} 档</span></span>`;
  },

  // 归类均值概率多线图（预测市场面板共用）：日期轴取并集，每归类一条线，y 轴 0–100%。
  // 用法：R.mkChart(id, c => R.probLines(clusters, c))（clusters 需带 series）
  probLines(clusters, colors) {
    const c = colors || R.colors();
    const dates = [...new Set(clusters.flatMap(x => x.series.map(p => p.date)))].sort();
    return R.lineOption({
      legend: { data: clusters.map(x => x.name), type: 'scroll', textStyle: { color: c.text, fontSize: 11 } },
      tooltip: { trigger: 'axis', valueFormatter: v => (v == null ? '—' : `${(v * 100).toFixed(1)}%`) },
      grid: { left: 44, right: 12, top: 36, bottom: 24 },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: { color: c.muted, fontSize: 10, formatter: v => R.md(v) },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: {
        type: 'value', max: 1,
        axisLabel: { color: c.muted, fontSize: 10, formatter: v => `${(v * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: c.grid } },
      },
      series: clusters.map(x => {
        const m = Object.fromEntries(x.series.map(p => [p.date, p.value]));
        return { name: x.name, type: 'line', showSymbol: false, smooth: true, lineStyle: { width: 2 }, data: dates.map(dt => m[dt] ?? null) };
      }),
    }, c);
  },


  // 图表默认项骨架（顶层浅合并；legend/grid 深一层）：统一 legend 样板与 grid 边距，
  // 页面只传差异部分：R.mkChart(id, (colors) => R.lineOption({ legend: { data: [...] }, series: [...] }, colors))
  // 图例色标（形状 + 取色）由 echarts-theme.js 的 reSyncLegend 统一推导，尺寸走主题默认 18×6。
  lineOption(over, colors) {
    const c = colors || R.colors();
    const base = {
      legend: { top: 4, right: 8, icon: 'rect', textStyle: { color: c.text, fontSize: 11 } },
      grid: { top: 36, left: 48, right: 16, bottom: 24 },
    };
    const out = Object.assign({}, base, over);
    if (over && over.legend) out.legend = Object.assign({}, base.legend, over.legend);
    if (over && over.grid) out.grid = Object.assign({}, base.grid, over.grid);
    reSyncLegend(out);
    return out;
  },

  // 建图 + 注册主题联动（Cmd+T 时自动重渲染）：按主题名 init（未显式配色的 series
  // 走主题 color 数组兜底，不再漏出 ECharts 默认紫）；切换时重注册 + dispose 重建
  // （ECharts 主题只在 init 生效，同实例 setOption 换不掉主题默认值）。
  // 重建后页面持有的旧引用会失效，故按 id 登记当前实例，页面用 R.getChart(id) 取最新。
  //
  // 空数据统一兜底（AGENTS.md「README 式空状态」）：所有 series 都无有效点时不画空坐标系，
  // 给原因 + 修复命令。单点守在这里，51 个 mkChart 调用页不必各自判空。
  // 口径：无 dataset/非数组 series/纯 graphic 图（已 grep 确认），故只看 series[].data；
  // 数组点（candlestick/OHLC）算有值，对象点看 value。
  _hasPoint(s) {
    return Array.isArray(s?.data) && s.data.some(
      v => v != null && v !== '' && (Array.isArray(v) || typeof v !== 'object' || v.value != null),
    );
  },
  isEmptyOption(opt) {
    const ser = opt && opt.series ? [].concat(opt.series) : [];
    return !ser.some(s => R._hasPoint(s));
  },
  mkChart(id, option) {
    const dom = document.getElementById(id);
    if (!dom) return null;
    dom.classList.add('skeleton'); // 骨架屏占位（app.css .skeleton）：首次 setOption 后摘除，主题重建不重复挂
    const render = () => {
      if (window.registerMacroTheme) registerMacroTheme();
      const opt = option(R.colors());
      if (R.isEmptyOption(opt)) {
        // 空态：不 init、不注册主题/resize（无实例可重绘）；只铺一次文案，避免主题循环重复写
        dom.classList.remove('skeleton');
        if (!dom.querySelector('.re-empty')) {
          dom.innerHTML = '<div class="re-empty">该指标暂无可用数据（未发布、超出回溯窗口或拉取失败）<br>'
            + '确认数据源后重新部署：对应 ./bin/fetch_* → gh workflow run deploy-pages.yml</div>';
        }
        return null;
      }
      dom.querySelector('.re-empty')?.remove(); // 空态文案先让位再 init（ECharts 要求容器为空，否则告警）
      const chart = echarts.init(dom, R.isDark() ? 'macroDark' : 'macro');
      chart.setOption(opt);
      dom.classList.remove('skeleton');
      R._charts.set(id, chart);
      return chart;
    };
    let chart = render();
    if (!chart) return null;
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

  // 覆盖小节内容但保留构建期渲染的小节标题。
  // 页面以前直接 `getElementById(id).innerHTML = ...` 填一个带 title 的 <Section>，
  // 会把 Section 自己的 <header>（标题 + accent 竖条）一起清掉 —— 实测 7 个页的
  // 「研判」段因此变成裸卡，page-toc 跳过去也没有视觉锚点。逐页手改会漏，收在这里。
  setSection(id, html) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll(':scope > :not(header)').forEach((n) => n.remove());
    el.insertAdjacentHTML('beforeend', html);
  },

  // 请求 + 错误处理
  async get(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  },

  // 可选端点：任何失败（404 / 网络 / JSON 解析）返回 null 而不抛。
  // 静态导出端 src/export_pages.py 的 _safe() 会跳过当日缺数据的端点，
  // 对应 JSON 可以合法不存在 —— 用 R.get 进 Promise.all 会让整个 await reject，
  // 一个源挂 = 整页空白。缺源段自己渲染空态（R.fail），其余段照常。
  async getOpt(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
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
    // scope="col"：屏幕阅读器才能把单元格读成「列头 + 值」（全站 re-table 单源，改这一处即覆盖各页大表）
    headers.forEach(h => { const th = document.createElement('th'); th.scope = 'col'; th.textContent = h; tr.appendChild(th); });
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
        // 默认格式化器：html 模式下必须转义 —— 未写 formatter 的列往往是名称/代码这类
        // 自由文本（来自 yfinance / Nasdaq / Wikipedia），它们会直接进 innerHTML。
        // 显式 formatter 仍自己负责（它们要返回着色 <span> 等 markup）。
        // textContent 模式不能转义：不解析 HTML，`&` 会被字面量显示成 `&amp;`。
        const asText = (v) => (html ? R.esc(String(v)) : String(v));
        const fmt = formatters[key] || ((v) => (v === null || v === undefined || v === '' ? '—' : asText(v)));
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
    // 统一应用：数值列的所有单元格（含 — 占位）与表头都右对齐，保证列内成线；
    // num 类另挂等宽字体（special.css .re-table td.num，timsun 数字排版纪律）
    pending.forEach(([td, j, isNum]) => { if (isNum || numCol[j]) { td.style.textAlign = 'right'; td.classList.add('num'); } });
    numCol.forEach((isNum, j) => { if (isNum) thead.rows[0].cells[j].style.textAlign = 'right'; });
    wrap.appendChild(table);
    return wrap;
  },

  // 数值卡片行（全站 KPI 卡单源）：items = [{label, value, sub, accent, cls, vcolor}]
  //   cls    附加到卡片（如 're-card-wrap' 长文本换行）；vcolor 只给 value 上色（var(--token) 引用）
  //   第二参 rowCls 附加到栅格行（如 're-cards-wide'）；页面不再写内联 grid-template-columns
  cards(items, rowCls) {
    const row = document.createElement('div');
    row.className = 're-cards' + (rowCls ? ' ' + rowCls : '');
    items.forEach(({ label, value, sub, accent, cls, vcolor }) => {
      const d = document.createElement('div');
      d.className = 're-card' + (accent ? ' re-card-accent' : '') + (cls ? ' ' + cls : '');
      d.innerHTML = `<div class="re-corridor-label">${label}</div>
        <div class="re-corridor-value"${vcolor ? ` style="color:${vcolor}"` : ''}>${value}</div>
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

// 主题由 TopicLayout（专题页）/ SPA 壳统一管理（localStorage 'ticker-toolkit-dark'，无偏好时跟随系统）；
// 本文件只需监听 theme-changed 重绘图表（见上方监听器）
