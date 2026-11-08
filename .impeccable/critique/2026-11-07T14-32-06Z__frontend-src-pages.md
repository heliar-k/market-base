---
target: frontend/src/pages/
total_score: 31
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 4
target_identity: "file:/Users/guankai/code/python/market-base/frontend/src/pages"
timestamp: 2026-11-07T14-32-06Z
slug: frontend-src-pages
---
# 观澜台专题页系统 — 设计评审

Method: dual-agent (A: critique-a · B: critique-b)

目标：`frontend/src/pages/` 下的页面系统，覆盖四个代表面：`/daily/`（今日头版）、`/rates/`（组入口）、`/credit/`（数据密页）、`/assets/equities/options/`（期权结构与 Wheel 雷达）。

## 设计健康分

| # | 启发式 | 分 | 关键问题 |
|---|---|---|---|
| 1 | 系统状态可见 | 3 | 徽章与时效做得极好，但 `/rates/` 整条 `#re-as-of` 渲染为空 |
| 2 | 贴近现实世界 | 3 | 术语准确，单位多数跟数字走；`10Y-2Y` 卡印 `+32.0` 没有 bp |
| 3 | 用户控制与自由 | 3 | 折叠、区间、子视图齐备；没有可回到的视图状态 |
| 4 | 一致性与标准 | 3 | 组件单源彻底；标题层级不统一，单位时有时无 |
| 5 | 错误预防 | 3 | 段级容错好；空值静默印「—」不给原因 |
| 6 | 识别优于回忆 | 4 | 共识子信号印在成员名下、口径折叠在卡内、图注写读法 |
| 7 | 灵活与高效 | 2 | 无快捷键、无 URL 状态、无「今日必读三项」锚点 |
| 8 | 美学与极简 | 3 | 无装饰噪声，但 credit 12 节 / 期权表 25 行同值徽章让一切一样响 |
| 9 | 错误识别与恢复 | 3 | 缺数据给到 `./bin/fetch_euro_sovereign` 这一步很好；部分空态只给「—」 |
| 10 | 帮助与文档 | 4 | 折叠口径 + 三层徽章 + 页脚来源 + 页面事实集，自用台里少见 |
| **合计** | | **31/40** | **Good（77.5%）** |

## 设计专属性判定

**外壳是通用仪表盘，决策内容才是这个产品的。** `.re-sec` 骨架、`.re-cards` KPI 卡行、`.page-toc` 胶囊条、`.re-duo` 双栏搬到任何 BI 看板都能跑；`/rates/` 的「结论 / 依据 / 触发」三栏卡、期权页的 GEX 三图，形状与任何一家 GEX 站点一致。

真正带身份的四样东西：

- **页头时效的分层徽章**：`数据截至 资产 2026-11-03 [披露] 利率/利差 2026-11-02 [披露] 流动性 2026-11-03 [估算]`。这是「口径可审计」这条硬约束唯一可见的形态。
- **三情景卡与证伪条件**（`daily/index.astro` 的 `#db-scenarios`）：命中项展开证据与推翻条件，未触发收进 `<details>`。一张允许被后来的自己推翻的判断卡。
- **Wheel 雷达表**（`options.astro` `#opt-pane-wheel`）：现价 / 单手 / Δ / IV / 年化 / 财报 / 禁卖原因 / 推荐合约落在同一行。
- **触发原因徽章**：`报警·债波压制 +1 项`、`情景·风险压力扩散 +2 项` 把「这张卡为什么热」写进标签。

可替换的具体位置：市场快照六板块的三列五行、KPI 卡行、12 个胶囊的页内目录、`finance shelf` 式 emoji（🌍🏦💳）。

**确定性扫描**：`impeccable detect --json frontend/dist`，退出码 2，201 条全部 warning（slop 46 / quality 155），扫描对象是 2026-11-04 的构建产物（比源码旧，位置按近似处理）。

| 规则 | 条数 | 命中 |
|---|---|---|
| `undersized-ui-text` | 148 | 37 个 HTML × 4 处：侧栏 10px 副名/分组标题/构建戳、10px 坐标刻度 |
| `marquee` | 38 | `.skeleton::after` 的骨架扫光 |
| `side-tab` | 8 | `.pm-alert` / `.db-scen-card` / `.db-alert-card` / `.db-line-card` 的左侧竖条 |
| `tiny-text` | 6 | 5 个 assets 页与 rates/pricing 的 11px 正文 |
| `cramped-padding` | 1 | `.status-bar` 子节点 |

判为误报：`marquee`（1.4s 横向扫光是骨架提示，不是自动滚动内容）；`cramped-padding`（28px 单行状态栏靠居中贴边是既定形状）；8 条 `side-tab`（本站用左侧 2-3px 竖条编码语义状态，是记号不是装饰）。`undersized-ui-text` 不是误报，但把 4 处 10px 放大成 148 条；DESIGN.md 明确把 10-11px 定为徽章、表头与坐标刻度档，这条与既定系统冲突，需要一次判断而不是逐条修。`tiny-text` 的 6 条 11px 正文为真。

## 视觉叠层

注入成功：B 在 4 个页面的 headless 上下文里注入了 `detect.js`，console 汇总为 `/daily/` 19 条、`/credit/` 15 条、`/assets/equities/options/` 15 条、`/rates/` 7 条。live server 已在收尾时停止（`live-server stop --keep-inject`），所以你的浏览器里没有常驻叠层。

叠层比静态扫描多给到的，是**复合透明度之后的真实对比度**（4 条，静态扫描读不到）：

- `2.1:1`（`#aab2ba` on `#ffffff`）：`.db-hv-member--absent` 的 `opacity: .45` 压住成员名（`special.css:1178`）
- `1.9:1`：同结构里 `small.db-hv-sub` 的「缺席」
- `3.7:1`：`.re-corridor-label` 的 `opacity: .7` 压住「当前阶段」「联邦基金有效利率」
- `3.4:1`：`.re-card-sub` 的 `opacity: .7` 把语义涨跌 chip 一起压暗（`--down` 文本档 `#c62828` → 实测 `#d76969`）

浏览器侧：四页 12 次载入，console error / warning / pageerror 全部为 0；390px 四页 `scrollWidth == innerWidth`，没有横向溢出；暗色 1440 正常套用，无残留亮色块。

## 总体印象

一套纪律非常硬的自用研究台：组件单源、口径可查、空态给修复命令、图表无障碍改写到位，这些在自建站里都是稀缺的。问题不在局部手工，而在**取舍**：信息密度是真实的，但「什么最响」经常交给数据偶然性（25 行同一个红徽章）或惯性（12 项粘顶胶囊），而真正的判断被放在第二屏。最大的机会是把 `/daily/` 的第一屏从「两张都说分歧的卡」改成一句可执行的结论。

## 成立的做法

1. **口径三层徽章 + 带修复命令的空态**：「数据缺失：… 先运行 `./bin/fetch_fred` 后重新导出」，来源、可信度、补救办法和读数在同一行。实测暗色下 11px 的 `.db-hv-sub` 仍到 5.18:1。
2. **三情景的证伪结构**：给假设、近 5 观测证据、推翻条件；未触发降为单行折叠。这就是「判断能被后来的自己复核」这条产品承诺的实现。
3. **Wheel 雷达的三态信号**（SELL PUT / 暂不操作 / SELL CALL）而不是红绿点，加「单手 $21k」，单笔风险在行内量得出来。

## 优先问题

### [P1] `/rates/` 页头没有数据截至，首卡空心

`rates/index.astro:55` 取 `data.overview.as_of`，而 `/api/rates/analysis` 的 `overview` 实测只有 `sections` 一个键：`as_of` 与 `effr` 都是 `null`。`R.setAsOf` 跳过空段，成品 `#re-as-of` 是空字符串；同一处第一张 accent 卡印 `—`、`sub` 为空串。

为什么算 P1：「口径可审计」是硬约束，利率族入口页现在读不到读数来自哪天，而 accent 位空着比缺卡更扎眼。

修：`overview.as_of || data.yield_curve.as_of`（后者有 `2026-11-02`）；`effr.value == null` 时不渲染该卡，把 accent 让给 2Y。

### [P1] 期权扫描表整列同值的红徽章

`assets/equities/options.astro` `#board` 的「结构 Regime」列，按当前快照 30 行里 25 行是同一个「负 Gamma + 正 Delta」。它成了全表最响的元素，Net GEX 的数字被压下去；扫 30 行要跳过 25 行相同红块。

修：删列，行内改成 4 字符号组（`γ− Δ+`），把「负 Gamma 25/30」写进小节副标；筛选胶囊已按正负分组，信息不丢。

### [P1] 手机端 149px 粘顶胶囊墙

`special.css` `.page-toc { position: sticky; top: 0 }` 加 credit 的 12 项目录：390×844 实测 149px（4 行 12 个胶囊），`scroll-padding-top` 161px。`credit` 页总高 6938px，滚动时图表卡上方常驻 3 行按钮，占视口 18%。

修：≤768 收成单行 `flex-wrap: nowrap; overflow-x: auto`，或折成「本页 12 节 ⌄」可展开。

### [P1] 缺席态与涨跌 chip 被复合透明度压到不达 AA

三处 `opacity` 把已经是文本档的颜色再压一档：`.db-hv-member--absent`（.45）落到 2.1:1，`.re-card-sub`（.7）把 `▼ -9bp` 压到 3.4:1，`.re-corridor-label`（.7）到 3.7:1。

为什么算 P1：`tests/test_semantic_text_contrast.py` 量的是 token 字面值，量不到叠加后的渲染值，所以这条永远不会变红；而「缺席」「当前阶段」正是判断要用到的信息。

修：这三处改用专门的弱化色（`--text-dim` 或语义色的 70% 混合值），不靠 `opacity` 压容器。

### [P2] 键值行与单位把「一项一个数」写坏了

`daily/index.astro:442` 的 `R.kvRows(triggered.map(...))`：value 是 `R.num(s.value)`，成品是光秃的 `2`；note 是 `${threshold} · ${as_of}`，于是「连续 2 季」在标签、阈值、读数里出现三遍，读数本身没有单位。三轨 auto-fit 在只有两项时还留出一条空轨（实测 533/533/0，标签到数值空 199px）。同页 `rates/index.astro:78` 的 `10Y-2Y` 用 `R.fmtBp` 输出 `+32.0`，而同一页 `bpText()` 别的行都带 `bp`。

修：value 用带单位的成品（`2 季`），note 只留观测日；两三项时不用三轨；`10Y-2Y` 换成 `bpText`。

## 人物红牌

**Alex（每天早读、没耐心）**：`/daily/` 第一屏两张共识卡都印「分歧」（参与 6/6 与 4/6），他要自己合成结论；期权页默认落 SPY 的结构视图，他的活是 NVDA/TSM 的 Wheel，要点两次且每天重来——`options.astro` 里 `let S='SPY'` 与 `let currentSubview='overview'` 是硬编码，全文没有 `location.hash` / `replaceState`。

**Sam（读屏 / 键盘 / 低视力）**：`/credit/` 实测 23 个同层 `H2`（12 个小节 + 11 个卡内图标题，「IG OAS 走势」与「利差水平」并列跳转级别）；`/rates/` 四段研判共 20 个标题。`/daily/` 专题卡的 emoji 没有 `aria-hidden`，读屏会念出表情名。四页 `.page-toc` 是裸 `div`，`aria-label` 均为 `null`。做得对的部分别丢：`th` 全部带 `scope`（0 缺失）、`skip-link` 在、焦点环实测 2px、ECharts 五张图描述是「标题 + 系列名」。

**Casey（手机、单拇指、随时被打断）**：`/credit/` 6938px、`/daily/` 5096px、`/options/` 3697px；粘顶胶囊墙 149px；期权页顶部工具条再占 182px，844 屏只剩约 500px 给数据；扫描表 1061px 塞进 296px 容器，要横滑才看到「操作」列。

## 次要观察

- 页头时效三段之间没有分隔符，成品读成「2026-11-03 披露 利率/利差」。
- `credit` 小节副标「Regime Score · 高分 = 压力更大」与卡内正文在 100px 内说两遍同一件事。
- 7 条 0-100 刻度条没有 50 / 75 刻度线，而整张卡的解释全押在这两个阈值上。
- `融资成本` 4 项落 3 轨，第二行只剩一项，成对的 HY−FF / HY−10Y 被拆开。
- 390 下「跨资产对照」表 `scrollWidth 320 / clientWidth 308`，「含义」列被切掉 12px。
- 8/10 专题卡标热，「有信号置顶」的排序几乎恒定，等于没有排序。
- `.db-topic-date` 只印 `08-01` / `10-28`，与页头 ISO 日期两种格式，跨年看不出年份。

## 可以想的问题

- 如果「分歧」是常态，共识卡每天给出的信息量是多少？要不要给「近 20 日分歧天数 / 上一次一致是哪天」？
- 8/10 标热，是筛选在工作，还是阈值太松？
- 期权页第一屏该回答「这个标的的结构如何」，还是「今天我能在哪个价位卖出什么」？
- 三情景卡是全页最贵的思想，为什么排在共识卡之后？
- 把 30 标的扫描降级成一张「今日异动表」，会损失什么？
