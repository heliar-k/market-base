---
name: 观澜台
description: 单人宏观研判与期权选点的读数台——结论先行，口径可查，亮暗两套等权
colors:
  paper: "#f6f9fc"
  card: "#ffffff"
  panel: "#f0f4f9"
  hairline: "#e6ebf1"
  hairline-soft: "#edf1f6"
  ink: "#1a2b42"
  ink-secondary: "#425466"
  ink-dim: "#5c6d84"
  border-strong: "#c8d4e0"
  tide-blue: "#1a73e8"
  tide-blue-ink: "#155ecb"
  tide-blue-wash: "#e8f0fe"
  rising-teal: "#17937f"
  rising-teal-ink: "#0f766e"
  ebbing-coral: "#e8494a"
  ebbing-coral-ink: "#c62828"
  buoy-orange: "#ff9800"
  buoy-orange-ink: "#9a5b00"
  mist-grey: "#9ca3af"
  mist-grey-ink: "#5b6472"
  hawk-red: "#ef4444"
  hawk-red-ink: "#b91c1c"
  dove-green: "#10b981"
  dove-green-ink: "#047857"
  chart-purple: "#9c27b0"
  chart-cyan: "#00bcd4"
  chart-cyan-ink: "#00838f"
  chart-violet: "#7c4dff"
  chart-slate: "#607d8b"
  night-paper: "#111217"
  night-card: "#181b1f"
  night-panel: "#22252b"
  night-hairline: "rgba(204, 204, 220, 0.2)"
  night-ink: "#f7f8f8"
  night-ink-secondary: "#d0d6e0"
  night-ink-dim: "#8a8f98"
  night-tide-blue: "#58a6ff"
  night-tide-blue-ink: "#a5d3ff"
  night-tide-blue-wash: "#1f2a3d"
  night-rising-teal: "#34d399"
  night-ebbing-coral: "#f87171"
  night-ebbing-coral-ink: "#fa8585"
  on-accent: "#ffffff"
  night-on-accent: "#0b1220"
typography:
  display:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "24px"
    fontWeight: 700
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "15px"
    fontWeight: 700
  title:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "14px"
    fontWeight: 700
  body:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.7
  label:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "12px"
    fontWeight: 400
  numeric:
    fontFamily: "JetBrains Mono, Menlo, Consolas, monospace"
    fontSize: "10px"
    fontWeight: 400
rounded:
  xxs: "2px"
  xs: "4px"
  sm: "6px"
  md: "8px"
  pill: "999px"
spacing:
  "2xs": "8px"
  xs: "12px"
  sm: "16px"
  md: "20px"
  lg: "28px"
  xl: "32px"
  page: "48px"
components:
  section-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px 20px"
  kpi-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px 20px"
    width: "275px"
  badge-readonly:
    backgroundColor: "color-mix(in srgb, {colors.mist-grey} 16%, transparent)"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.xs}"
    padding: "0 6px"
    height: "18px"
  pill-nav:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
    height: "24px"
  pill-nav-active:
    backgroundColor: "{colors.card}"
    textColor: "{colors.tide-blue-ink}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  range-button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.xs}"
    padding: "4px 10px"
  range-button-active:
    backgroundColor: "{colors.tide-blue}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.xs}"
    padding: "4px 10px"
  table-header:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-dim}"
    padding: "8px 12px"
  table-row:
    textColor: "{colors.ink}"
    padding: "7px 12px"
  nav-item:
    textColor: "{colors.ink-secondary}"
    rounded: "0px"
    padding: "6px 16px"
    width: "240px"
  nav-item-active:
    backgroundColor: "{colors.tide-blue-wash}"
    textColor: "{colors.tide-blue-ink}"
    padding: "6px 16px"
---

# Design System: 观澜台

## Overview

**创意星柱：「观澜台」**

高台观潮。整个界面是一处安静的观潮台：读数像台上的刻度，结论像旗语，风浪本身不在画面里表演。潮起潮落交给颜色的语义档去说（涨潮青、退潮彤），水面交给中性纸底，台子交给 1px 的界线与 8px 的圆角。装饰到此为止，剩下的版面全让给数据。

密度按仪表盘走：正文 13px、标签 12px、坐标刻度 10px，一张卡片里常常同时有标题、读数、单位、时效与注脚。窄字号能让「一屏之内能同时看见多少条读数」这个指标尽量高——这是单人研判台最实际的一项能力。

亮色与暗色是等权的两套。亮色取冷蓝白的纸感（页面底、卡片、浮层三层明度阶梯），暗色取深灰的夜盘感（同样的阶梯在低明度区间重排）。任何新组件必须在两套下各自成立；验收时两套都要看。

**Key Characteristics:**
- 结论先行：每页第一屏是状态、评分、报警与一句话，图表与明细在下层。
- 语义色两档：同一个语义同时有「图形档」与「文字档」，各管各的对比度门槛。
- 方胶囊表示只读状态，圆胶囊表示可点的导航与页头时效，形状本身区分能否操作。
- 卡面自带 1px 界线，卡内不再叠第二层框。
- 数字用等宽字，正文用系统字，两套字体分工明确。
- 断点只有 1024 与 768 两档管页面骨架，不新增中间档。

## Colors

整套色板分成四种职责：一种品牌色定调，三个市场语义色说涨跌与警戒，五个分类色给多序列图分线，一层中性纸墨承载全部版面。

### Primary

- **澜青**（`colors.tide-blue` / `colors.night-tide-blue`）：品牌色，也是全站 accent。用在选中态的实底（`.range-btn.active`）、焦点环、导航当前项左侧 2px 竖标、小节标题的 3px 竖条、图表配色板的第 1 位。它在页面上占比很小，出现即表示「这里可以点」或「这一段从这里开始」。
- **澜青墨**（`colors.tide-blue-ink` / `colors.night-tide-blue-ink`）：澜青作正文色时的可读档。链接、导航当前项文字、图表外的强调文字都用它。澜青本身在白卡上 4.51:1、在页面底 4.26:1，做文字不达 4.5 的门槛，所以两档分开取令牌。暗色下换成更浅的同色相蓝——满饱和纯蓝在深底小字号下边缘发毛。
- **澜青淡底**（`colors.tide-blue-wash` / `colors.night-tide-blue-wash`）：导航当前项底、选中胶囊底、浅色浮层底。

### Secondary

三个语义色成对出现，每个都有图形档与文字档：

- **涨潮青**（`colors.rising-teal` / `colors.night-rising-teal`）：上涨、正向、注入。K 线阳线填充、图表里表示改善的序列、`td.up` 与 `.up` 类。
- **涨潮墨**（`colors.rising-teal-ink` / 暗色别名回原语）：上涨语义作文字时的档位。
- **退潮彤**（`colors.ebbing-coral` / `colors.night-ebbing-coral`）：下跌、负向、抽离。与涨潮青严格成对。
- **退潮墨**（`colors.ebbing-coral-ink` / `colors.night-ebbing-coral-ink`）：暗色下退潮彤压在同色 22% 徽章底上只有 4.42:1，所以暗色单独取一档更浅的 `#fa8585`。
- **浮标橙**（`colors.buoy-orange` / `colors.night-buoy-orange`）：警戒、待确认、MA20 与 MACD 信号线。中间档两色（`--color-warn-deep` / `--color-warn-light`）只做色带与填充，不作文字色。
- **浮标墨**（`colors.buoy-orange-ink` / 暗色别名回原语）：警戒语义作文字时的档位。
- **鹰红 / 鸽绿**（`colors.hawk-red` / `colors.dove-green`）：美联储专题专用的收紧与宽松语义，与涨跌色相互独立，同样各带一个文字档。

### Tertiary

图表分类色，只用于多序列图的第 5–8 位，不进正文：

- **图表紫**（`colors.chart-purple`）、**图表青**（`colors.chart-cyan`，文字档 `colors.chart-cyan-ink`）、**图表靛**（`colors.chart-violet`）、**石墨青**（`colors.chart-slate`）。
- 序列色板固定 8 位：澜青、浮标橙、涨潮青、退潮彤、图表紫、图表青、图表靛、石墨青。前四位是语义色，所以单序列图默认就是澜青，双序列图默认是「澜青 vs 浮标橙」。

### Neutral

- **纸**（`colors.paper` / `colors.night-paper`）：页面底。亮色是冷调的极浅蓝白，暗色是近黑的深灰。
- **卡面**（`colors.card` / `colors.night-card`）：卡片、导航栏、状态栏、输入框底。
- **浮层**（`colors.panel` / `colors.night-panel`）：表头色带、次级填充、tooltip 半透明底的基础色、嵌套面板。
- **界线**（`colors.hairline` / `colors.night-hairline`）：1px 分隔线。更弱一档的 `hairline-soft` 只用于表内行分隔与图表网格线。强档 `border-strong` 只给需要「看得见」的边框（悬停态描边、虚线空态框）。
- **墨**（`colors.ink`）、**次墨**（`colors.ink-secondary`）、**淡墨**（`colors.ink-dim`）：三层文字阶梯。标题用墨，正文与表格数据用次墨，注脚、表头、轴标签、单位用淡墨。四层以外的文字淡化一律用 `opacity`，不新造灰。

### Named Rules

**两档色规则。** 每个语义色都有一对令牌：不带后缀的原语只准出现在填充、描边、线条、色带上（门槛是 3:1）；带 `-text` 后缀（或通过 `--up` / `--down` / `--flat` / `--warning` 这四个短名）的那一档才准出现在 `color:` 上（门槛 4.5:1）。改一个语义色要连着改两档，并且两档都要过各自的底。

**方与圆规则。** 只读的状态、标签、层级徽章一律 4px 方胶囊、18px 高、11px 字；可点的导航与筛选胶囊一律 999px 圆胶囊、至少 24px 高。页头时效区是唯一的例外：`.re-as-of`、`.re-tier`、`.cons-intraday` 这类胶囊虽只读，仍保留 999px 圆角——它们与页头标题同行，在视觉上属于「页」而不属于「数据」。读者靠形状判断「这个能不能点」，例外只有这一处。

**等权双主题规则。** 亮暗两套令牌成对定义，谁也不从属于谁。新增颜色必须在 `:root` 与 `body.dark` 两侧同时给出，不能只写一侧然后靠继承凑合。

## Typography

**Display / Body Font:** system-ui（`-apple-system` 兜底）
**Numeral Font:** JetBrains Mono（自托管 latin 子集，400 / 500 / 700，`/vendor/fonts/`）

**Character:** 正文用系统字，让中文在 macOS 上落到苹方、在 Windows 上落到微软雅黑，不为字形统一引入 200KB 的网络字体。数字单独走等宽：JetBrains Mono 比 Menlo 紧一档，读数、坐标刻度、tooltip 里的数值纵向一列时小数点对得上——这是研判台最常被反复扫读的东西。

### Hierarchy

- **Display**（700，24px，行高 1.25，字距 -0.01em）：只用于页头标题（`.re-header h1`）。负字距让长专题名在窄屏换行时不散；`text-wrap: balance` 防孤字。
- **Headline**（700，15px，行高 1.4）：小节标题。左侧固定一条 3px 澜青竖条作「段落起点」的视觉锚。标题不折行，宽度不够时先让副标换行、再让徽章换行。
- **Title**（700，14px）：卡片内的标题（`.re-section h2`）。比小节标题低一档，用来区分「这一节」与「这一张卡」。
- **Body**（400，13px，行高 1.7）：正文段落、叙事、表格数据。行高 1.7 是为中文长段落定的；表内不用这个行高，行高由单元格内距控制。
- **Label**（400，12px）：副标、时效、注脚、单位、表头。颜色取淡墨。这一档是全站出现次数最多的字号。
- **Numeric**（400，10px，等宽）：图表坐标刻度。tooltip 与卡内大读数用同一字体放大到 12–22px。

### Named Rules

**等宽只顾数字。** JetBrains Mono 只出现在读数、坐标刻度、tooltip 数字与日期上。段落正文、标题、按钮文字一律系统字——等宽字排中文会散架。

**一级一档。** 常用阶梯是 10 / 11 / 12 / 13 / 14 / 15 一组、16 / 18 / 20 / 22 / 24 / 26 一组。半档（11.5 / 12.5 / 13.5 / 14.5）只出现在密集面板与叙事正文里，用来在两档之间挤出一档过渡；新增字号先看整数档够不够，不够再用半档，不用 12.3 这类随意值。

**小字下限。** 10px 只给侧栏说明（品牌副名、分组标题、构建戳）与页头口径徽章：240px 宽的侧栏要在一屏里放下 12 个专题组，这些字不承载读数。11px 给徽章、表头、图表坐标刻度与源行（`.re-source`）。正文段落、表格数据、卡片读数不低于 12px。判定标准是「读者需不需要在 10px 里读出数字或做判断」：需要就升档，不需要就留在 10px。

## Layout

内容区是单列纵排的阅读流：左侧固定 240px 专题树，主栏居中，宽度取 `min(1200px, 100vw - 64px)`，内距 28 / 32 / 48（上 / 左右 / 下）。一页之内从上到下是「页头（标题 + 数据截至胶囊）→ 目录胶囊条 → 一列小节」，小节之间 32px，小节内部块与块之间 16px，标题到内容 14px。

宽屏的小节里再用两类排布：卡行（`.re-cards`）用 flex 平分增长、到 275px 封顶，≥4 张卡铺满整行，2–3 张卡左对齐留白；相邻两张窄卡用 `.re-duo` 并成两列（轨道下限 340px，窄于该值自动回落单列）。表格卡内单元格横向内距 12px，表头色带向卡片两侧出血到边。

图表高度用 `clamp(220px, 40vh, 300px)`：上限就是原来的固定高度，视口高 ≥750px 时与桌面版逐像素一致，只有矮屏与手机变矮。

### Named Rules

**两档断点。** 页面骨架只有两档：**1024px**（及以下收起左侧树、双栏堆叠）、**768px**（及以下多列栅格塌单列、装饰性文字隐藏）。列表密度与个别面板另有 600 / 480 / 900 三档，它们只改字号与密度、卡片列数旋钮、单块显隐，不承担页面骨架。新增骨架一律挂 1024 / 768，不引入 1100 这类中间档；归并旧查询时只允许往「更早塌列」的方向走。卡片内部的宽度判断走容器查询（`container-type: inline-size`），因为侧栏宽度或内容上限一改，按视口写的阈值就静默失效。

**一项一个数。** 一组「名称 → 读数」的短列表走键值行（2–4 列自适应），不写成两张列的表；表格只给真正需要行列对照的数据。键值行的注脚挂在名称后面，不挂在数字后面——数字右对齐才能形成一条竖向可扫的读数线。

## Elevation & Depth

亮色靠明度阶梯加极淡的投影：页面底 → 卡片（白）→ 浮层（`panel`）→ tooltip（浮层色 88% 不透明度 + 8px 背景模糊）。卡片常驻一道 `0 1px 2px rgba(16,35,61,.05), 0 1px 6px rgba(16,35,61,.04)` 的投影，贴着底不飘；浮层按钮与弹层再各重一档。

暗色没有任何投影：`--card-shadow: none`。深度全靠四级明度（`#111217` → `#181b1f` → `#22252b` → 半透明 tooltip）与 `rgba(204,204,220,.2)` 的透明白描边。在深底上投影看不见，堆上去只会让边缘发脏。

### Shadow Vocabulary

- **card-shadow**（`0 1px 2px rgba(16,35,61,.05), 0 1px 6px rgba(16,35,61,.04)`）：亮色卡片常驻。
- **shadow-float**（`0 1px 3px rgba(16,35,61,.10), 0 4px 12px rgba(16,35,61,.08)`）：浮层按钮、伴读浮钮。
- **shadow-pop**（`0 4px 16px rgba(16,35,61,.14), 0 1px 3px rgba(16,35,61,.10)`）：tooltip、钻取面板。
- 暗色三档压成一条：`--shadow-float: 0 2px 10px rgba(0,0,0,.5)`、`--shadow-pop: 0 4px 16px rgba(0,0,0,.6)`、卡片无影。

### Named Rules

**单框规则。** 卡面已经有一道 1px 界线，卡里的图表、表格、内嵌块不再画第二道边框，也不加第二个圆角。嵌在卡内的图用 `.re-plain` 去掉自带描边；需要框的只有「直接放在页流里当卡面」的图。

**亮色有影、暗色无影。** 新增组件时不要给暗色补一套投影，往明度阶梯里加一级更准。

## Shapes

圆角四档：2px（极小元素，含图表柱顶与竖条两端）、4px（按钮、徽章、输入框、可点小控件）、6px（跳过导航）、8px（卡片、图表、空态框、tooltip）、999px（导航与筛选胶囊、数据截至胶囊）。形状与可点性挂钩：4px 表示「状态」，999px 表示「能点」。

界线只用 1px，颜色分别是界线、更弱的界线、强界线三档。没有 2px 以上的实线边框（焦点环 2px 是例外，见下）。强调形态靠「一根竖条」：小节标题 3px×14px 的澜青竖条、导航当前项 2px 通栏竖标、`.sig-row` 每格 2px 竖标。

## Components

### Buttons

- **Shape:** 4px 圆角，1px 描边（`btn-border`），白底。
- **Primary（选中态）：** 澜青实底 + 反色文字（`colors.on-accent` 亮色为白，`colors.night-on-accent` 暗色为深墨）。用于区间预设、筛选标签的当前项。
- **Hover / Focus:** 悬停只做透明叠加（`hover-tint`）+ 描边换成强档，不换色相；焦点环统一 2px 澜青、外扩 1px。按下时 `translateY(1px) scale(.985)`，给一点物理反馈。
- **Secondary / Ghost：** 表格行内的操作与抽屉里的次级按钮走无底无框的文字按钮，颜色取澜青墨。

### Chips

- **Style:** 只读徽章（`.re-badge`、`.opt-chip`、`.sw-tag` 一族）一律 4px 方胶囊、18px 总高、11px 字、无衬线；底色是该语义色的 16%（暗色 22%）透明叠层，描边是同色 25–40%。页头时效胶囊（`.re-as-of` / `.re-tier`）例外，走 999px 圆角。
- **State:** 语义靠色阶传达：涨/正面走涨潮青，跌/负面走退潮彤，待确认走浮标橙，中性走雾灰。可点的导航胶囊另走 999px 圆胶囊。

### Cards

- **Corner Style:** 8px。
- **Background:** 卡面取 `card`；表头色带与嵌套面板取 `panel`。
- **Shadow Strategy:** 见「纵深」——亮色一道 card-shadow，暗色无。
- **Border:** 1px `hairline`。
- **Internal Padding:** 16px × 20px（普通卡）、12px × 14px（密集卡），窄屏降到 12px × 14px。

### Tables

- **Style:** 表头 `panel` 底、12px 淡墨、600 字重、粘顶；行 12px 横向内距、行间 1px 弱界线；数值列右对齐并启用 `tabular-nums`。
- **Behavior:** 表头色带向卡片两侧出血到边，首列文字回到内容栅格线。极值走语义色文字档，整列同值的观测期不单独占一列。

### Navigation

- **Style:** 左侧 240px 固定树，卡面底 + 右侧 1px 界线。品牌区上距 16px，名称 16px / 700 / 0.08em 字距，副名 10px / 0.12em。分组标题 10px、大写、0.16em 字距。
- **States:** 条目 13px / 500 次墨；悬停换 `surface-hover` 底；当前项 `tide-blue-wash` 底 + 澜青墨文字 + 左侧 2px 通栏竖标。二级条目缩进到 32px 并降到 12px，三级 46px / 11px。
- **Mobile:** ≤1024 收进抽屉，由 ☰ 唤出，遮罩层 z-index 低于导航层。

### Charts（标志组件）

- **Geometry:** 栅格上 48px（留给图例）、其余 3–4%、`containLabel: true`；图例统一 18×6 的自绘 path，按线型选形状（实线 / 虚线 / 点线 / 点划线 / 阴影带 / 带圆点实线），页面不手写图例图标。
- **Line width:** 三档——2（主序列）、1.5（次级与虚线对比）、1（参考线与零轴）。窄屏（画布 <480px）统一乘 0.7。
- **Axes:** 日期轴一律 `type: 'time'` + 点对数据，不写 `axisLabel`（字号、颜色、等宽字体由主题给）；类目轴只给真正的类目（期限、行权价、矩阵）。
- **Tooltip:** 8px 圆角、`panel` 88% 底 + 8px 模糊、`shadow-pop`、12px 等宽字。
- **Accessibility:** 主题层统一开 `aria.enabled`，描述改成「小节标题 + 系列名」，不朗读原始数据点；图表一律经 `R.mkChart` 初始化，绕开它会漏掉这条。

## Do's and Don'ts

### Do

- **Do** 语义色成对取令牌：填充用原语，`color:` 用 `-text` 档；改色时两档一起改并各测各自的底。
- **Do** 把只读状态做成 4px 方胶囊、可点控件做成 999px 圆胶囊，触控命中区不小于 24px。
- **Do** 每页第一屏给结论（状态条、评分、报警、一句话），图表与明细放下面一层，两层互相联动。
- **Do** 卡面用 8px 圆角 + 1px 界线 + 16/20 内距；卡内图表去掉自带描边。
- **Do** 数字用 JetBrains Mono + `tabular-nums`；「名称 → 读数」短列表走键值行，读数右对齐成一条线。
- **Do** 页头时效统一写成「数据截至 …」，由 `R.setAsOf` 写入，并挂上披露 / 估算 / 代理三层徽章之一。
- **Do** 亮色与暗色两套都实际打开看一遍再交付；图表主题切换后要重新注册。
- **Do** 深色下靠明度阶梯造纵深，不要补投影。

### Don't

- **Don't** 用营销页的节奏：大字号渐变标题、插画、夸张的章节间距。
- **Don't** 用交易终端的炫技：满屏红绿闪烁、超大数字、滚动动画。
- **Don't** 走极简文献风：一屏只放三个数、大片留白、圆角贴纸式装饰。
- **Don't** 把满饱和的图形档颜色用在 `color:` 上（澜青在白卡上 4.51:1、页面底 4.26:1，涨潮青白卡 3.81:1）。
- **Don't** 在卡里再套一层带边框与圆角的框。
- **Don't** 给页面骨架新增 1024 / 768 以外的断点，也不要用 1100 这类中间档。
- **Don't** 给折线写 1.2 / 1.6 / 2.2 / 2.5 / 3 这类线宽，只有 2 / 1.5 / 1 三档。
- **Don't** 用 `type: 'category'` 装 ISO 日期串，日期轴一律走 `time` 轴。
- **Don't** 把 10px 用在任何读数、正文或需要判断的文字上（它只给侧栏说明与页头口径徽章）。
- **Don't** 把域内色值写进页面内联样式：所有颜色走 `var(--token)`，DOM 里不出现解析后的十六进制。
- **Don't** 自造并行的组件（新的卡片、徽章、数字格式器），复用 `Section` / `R.cards` / `R.kvRows` / `R.table` / `R.num` / `R.chgSpan`。
