# 观澜台 Web UI/UX 优化计划

> 输入：全仓库前端审计（static/ 下 1 SPA + 31 专题页）+ 业界金融数据平台设计标准调研。
> 范围：仅 UI/UX 层，不动数据管道与分析引擎。
>
> **执行状态（workflow wf_77f3323cd92d 已完成）**：P0/P1/P2 已全部实施，25 文件 +320/−249，pytest 755 全绿。P3 未做。
> 实施偏差记录：① P1-5 等宽数字经核实现状已满足（--font-mono 与 tabular-nums 早已存在），无改动；② crypto.html 内联样式实为 JS innerHTML 一处，「102 行」为审计时过时估计；③ --z-toast 无使用方按 YAGNI 未定义。

---

## 0. 现状总评

**基建执行到位率约 90%**——tokens.css 语义色单源、SITE_NAV 导航单源、`R.asOf` 时效格式、`R.mkChart` 图表一体化、reSyncLegend 图例对齐这五条单源约定基本守住了，32 个专题页骨架高度统一。这在同类个人项目里属于上游水平，**不需要推倒重来**。

缺口集中在三块：

1. **移动端适配**——断点不成体系，tech 视图三栏在手机上完全不可用
2. **可访问性（a11y）**——全站 0 个 aria 属性、导航不可聚焦、overlay 无 Esc
3. **暗色主题下的硬编码残留**——8 处硬编码颜色，其中 2 处在暗色下显示错误值（误导性 bug）

业界对标：Bloomberg/Refinitiv/TradingView/Koyfin 的共性是**暗色优先、等宽数字字体、高密度无装饰表格、涨跌色标语义化、两米外可读**；2026 通用仪表盘标准是**3 秒扫描（顶部放结论）、分组降认知负荷、空状态有引导、数据时效透明、WCAG AA 对比度 ≥4.5:1**。观澜台的「先看结构再看数据」双层节奏和 `R.asOf` 时效透明已经与业界对齐，下面只列差距项。

---

## P0 — 修复类（bug 级，先做）

| # | 位置 | 问题 | 动作 |
|---|------|------|------|
| P0-1 | `assets/equities/options.html:202-203` | 手写图例硬编码亮色 `#26a69a/#ef5350`，暗色下与图上 token 色 `#34d399` **不一致，显示错误信息** | 删掉手写 legend，改走 `R.lineOption` / `reSyncLegend` |
| P0-2 | `daily/index.html` | **首页无 catch/错误态**，API 挂即白屏 | 补 `R.fail` 错误态（30/32 页已有现成模式） |
| P0-3 | `volatility/index.html:197-199` | 热力图 `rgba(38,166,154)/rgba(239,83,80)` 硬编码亮色值，暗色下色偏 | 色值入 tokens.css 派生，或从 `getComputedStyle` 读 |
| P0-4 | `cross-correlation.js:320-321` + `assets/index.html:167` | 热力图标签硬编码 `#1f2937/#fff`；叠加 `MACRO_COLORS` 模块加载时快照（macro-common.js:4），**主题切换后颜色过期** | `MACRO_COLORS` 改为函数惰性取值；硬编码色改读 CSS 变量 |
| P0-5 | `app.css:47-49, 246` | tech 视图三栏（140px sidebar + 320px panel）≤900px 无任何收敛，**手机上技术分析不可用** | 最懒方案：媒体查询下三栏改纵向堆叠，panel 折成可展开抽屉；不重写交互 |

业界依据：颜色语义一致性是金融 UI 的底线（Bloomberg 体系「cyan/coral 买卖信号全站统一」）；首页白屏违反「空状态/错误态是被浪费的设计时刻」。

## P1 — 体验类（感知最强）

| # | 位置 | 问题 | 动作 |
|---|------|------|------|
| P1-1 | 全站 | loading 是纯文本「加载中…」，无骨架屏 | CSS-only 骨架屏：给 `.chart-card` 加 `skeleton` 修飾类（ shimmer 渐变动画 ~20 行 CSS），JS 在数据到达后移除。不引入库 |
| P1-2 | 全站 | **0 个 aria-\*、0 个 tabindex**；导航项是无 href 的 `<a>`（macro-view.js:101-104）不可聚焦 | 导航 `<a>` 补 `href="#..."` + `role`；当前页加 `aria-current="page"`；按钮语义化 |
| P1-3 | `cross-correlation.js:376` 等 | drill overlay 只能点遮罩关闭，无 Esc、无 focus 管理 | 统一 overlay 关闭逻辑：Esc + 遮罩点击 + 关闭按钮，focus trap 可后置 |
| P1-4 | `special.css` | 断点 640/700/900/1100 四条线散落不成体系；`vol-cross-chart` 固定 680px 高（volatility/index.html:74） | 断点收敛为 2 档 token（如 768/1024，写进 tokens.css 注释约定）；图表高度改 `clamp()` 或视口百分比 |
| P1-5 | 全站 | 数字字体未等宽，表格/统计卡数字跳动、列不对齐 | tokens.css 加 `--font-mono`（`ui-monospace, SF Mono, ...`），`.dash-stat`/`.diag-row`/数据表数字列应用 `font-variant-numeric: tabular-nums`（1 行 CSS，零成本对齐 Bloomberg 惯例） |
| P1-6 | `fed/index.html:33-36` | echarts/rates-common 脚本放 body 尾部，其他页在 head | 归入 sync_pages_head 模板统一 |

业界依据：「两米外可读」（等宽 tabular-nums 是终端级产品的标配）；WCAG AA 可访问性；loading 态决定感知性能。

## P2 — 结构类（还债，防腐化）

| # | 位置 | 问题 | 动作 |
|---|------|------|------|
| P2-1 | `assets/crypto.html`(102行)、`rates/pricing.html`(60)、`assets/fx.html`(28)、`daily/index.html`(15)、`equities/options.html`(7) | 5 页内联 `<style>` 共 212 行，违反 special.css 单源头注 | 逐页收编进 special.css 或页面级 css 文件 |
| P2-2 | `app.css:208/334/427` | 按钮类四套平行定义（`.range-btn`/`.macro-range-btn`/`.stock-btn`/`.correlation-preset-btn`），样式近乎相同；`.macro-range-btn` 零使用 | 归并为 `.range-btn` 单类 + 修饰类；删 `.macro-range-btn` |
| P2-3 | `app.css:175-195` | `.dash-liq-*`/`.dash-indices` 注释自认「已退役」未删 | 直接删 |
| P2-4 | `liquidity/rrp-tga.html:40`、`subsurface.html:45`、`rates/fed-funds.html:99` | `#ff7043/#ffb74d/rgba(59,130,246,.08)` 硬编码警示/品牌色 | 入 tokens.css 派生变量 |
| P2-5 | `treasury/index.html:145` | 手写 legend `icon:'circle'` 10×10，偏离主题 18×6 样板 | 走 reSyncLegend；test_legend_sync.py 已会拦 |
| P2-6 | `app.css:313/329/374` | z-index 魔法数 30/40/50/60 散落 | tokens.css 定义层级 token（`--z-nav/--z-overlay/--z-toast`） |
| P2-7 | `dashboard.js:373` | `esc()` 与 `R.esc` 重复实现 | 删本地版，用 `R.esc` |
| P2-8 | page-toc 三种填法 | fed-funds 硬编码 HTML、部分页 JS 填、geo/labor 没有 | 统一为 JS 从 `data-toc` 属性生成；geo/labor 补上或明确不需要 |

## P3 — 进阶级（业界趋势，可选）

按 ROI 排序，做不做取决于投入意愿：

1. **Dashboard 首屏结论层强化**（业界「3 秒扫描」+「data storytelling」）：dashboard 已有 dash-stat 结论卡，可再加一行「今日一句话」——已有 9 个规则引擎，daily 页的研判结论摘要提到 SPA 首屏。纯展示层拼接，成本低。
2. **密度模式切换**（Koyfin/终端惯例）：tokens.css 已有变量基础，加 `.density-compact` 作用域类改 padding/字号即可，~30 行 CSS。
3. **移动端手势/bottom-sheet**（2026 趋势）：tech 视图 P0-5 堆叠后的自然延伸，等移动端有人用了再说。
4. **SPA macro 视图去 iframe 化**：macro-view.js 用 iframe 懒加载专题页，带来双滚动条、主题同步、键盘焦点穿透问题。这是架构级改动，**工作量大收益渐进，建议搁置**直到 P0-P2 消化完再评估。

## 不做的事（刻意排除）

- **不引入 CSS 框架 / 组件库**：现有变量体系已够用，引入 Tailwind/Bootstrap 是净负债
- **不做拖拽自定义 dashboard**（Koyfin 式）：个人站受众固定，YAGNI
- **不做亮暗以外主题**：双主题已覆盖
- **不重写导航三套并存**：SITE_NAV 单源治理已生效，P1 只补可访问性属性，不动结构

## 验收与护栏

- 新增回归测试：P0-1/P0-4 硬编码颜色进 `test_static_single_source.py` 或新 grep 型测试（扫描 static/ 下十六进制色值白名单外出现）
- 全量改动后跑 `uv run python -m pytest`（现有 547 测试含 legend/head 单源护栏）
- 视觉验收：亮暗双主题各过一遍 P0 涉及的 6 个页面

## 建议执行顺序

P0（5 项，约半天）→ P1-5 等宽数字（1 行 CSS，顺手）→ P1 其余（1 天）→ P2 还债（1 天，可与 P1 穿插）→ P3 按需。

---

---

## 附录：业界调研来源

- [Fintech UX Design: 10 Best Practices for Dashboards 2026 — wildnetedge](https://www.wildnetedge.com/blogs/fintech-ux-design-best-practices-for-financial-dashboards)（颜色语义、视觉层级、分组降认知负荷）
- [Fintech UX Design Guide 2026 — fuselabcreative](https://fuselabcreative.com/fintech-ux-design-guide-2026-user-experience)（data storytelling > data visualization、mobile-first 手势）
- [Dashboard UI Design Guide 2026 — designstudiouiux](https://www.designstudiouiux.com/blog/dashboard-ui-design-guide)（WCAG AA 对比度 ≥4.5:1、主题切换、导出）
- [Dashboard Design Guide 2026 — aufaitux](https://www.aufaitux.com/blog/dashboard-design-examples-inspiration-best-practices)（3 秒扫描原则、空状态设计、数据时效透明）
- [Dashboard Design Principles — UXPin](https://www.uxpin.com/studio/blog/dashboard-design-principles)（图表类型匹配数据目的、网格布局）
- [Dashboard Design System — fuselabcreative](https://fuselabcreative.com/dashboard-design-system)（chart tokens、表格行为、密度模式）
- [Trading Terminal design system — opendesigner](https://opendesigner.io/design-systems/trading-terminal)（Bloomberg 系共性：暗色优先、等宽数字、高密度无装饰、买卖色标、两米外可读）
- [Koyfin — Bloomberg alternatives](https://www.koyfin.com/blog/best-bloomberg-terminal-alternatives)（可定制工作区、主题切换、现代界面定位）
