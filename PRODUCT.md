# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

单人使用：作者本人。每天打开它做两件事——宏观研判定仓位、期权与 Wheel 选点。站点公开部署在 https://market-base.pages.dev ，公开访问是顺带结果，不引导取舍：页面不为陌生读者增设解释层，也不为增长或留存做任何设计让步。

## Product Purpose

把美股、宏观与期权面的数据每天变成一份可直接执行的盘面判断，供作者本人定仓位、选期权点位。好用的样子有四个：

- 早上十分钟读完 `/daily/` 就知道今天做什么；
- 每个决策都能指着页面上的读数说出它的来源与时效；
- 过一段时间能拿历史快照回头验证当时的判断；
- 新的数据源或新的分析能很快接进来，不必改动一大片。

## Positioning

不设外部对标产品。站点与数据口径独立演化，按作者自己的判断需要生长。

## Operating Context

- 数据每日自动更新：GitHub Actions 的 `daily-full`（北京 05:00）拉全部不依赖 IBKR 的源并提交；`evening-late`（23:15）补下午才发布的 FRED / CBOE / FSI；`intraday-refresh` 盘中每 15 分钟刷价量；`crypto-hourly` 全天候刷加密三源。
- 依赖 IBKR / TWS 的源（期权链、商品曲线、指数与个股日线的权威覆盖）在本地机器上手跑。
- 看盘节奏：盘前复核实时价与期权墙 → 盘中看价量 → 盘后读专题页与 Wheel 看板。
- 交付面有四个：静态站点（Cloudflare Pages）、主站 SPA 两视图（技术分析 / 关联）、TUI 双模式（技术分析 / 宏观）、CLI 分析脚本。

## Capabilities and Constraints

已经建成、可以直接引用的部分：

- 数据管道：FRED 13 分类 91 系列、CBOE 波动率、OFR 压力指数、Treasury 拍卖与 DTS、CFTC COT、FINRA 做空量、SEC 原文与 Form 4、Barchart 期货曲线与波动率快照、IBKR 期权链、yfinance 资产价、加密衍生品与 ETF 资金流；`data/` 下约 789 MB 历史 CSV/JSON。
- 派生分析层：宏观派生（2s10s / 净流动性 / BEI / SOFR-IORB）、期权结构（GEX / DEX / Vanna / Charm）、Wheel 看板、跨资产相关性、共识层两级投票（ADR-0004）、规则引擎叙事（9 个专题分析模块）。
- 呈现层：37 个 Astro 页面、主站 SPA、伴读抽屉（LLM 问答，代理跑在 Cloudflare Pages Functions）。
- 工程形态：Python 3.13 + uv；pytest 124 个测试文件；数据写入一律 upsert，重复跑不产生脏行。

必须长期守住的约束：

- **数据口径可审计**：每条读数标明数据源与「披露 / 估算 / 代理」层级；口径有争议时取保守值，缺数据如实说缺，不编造连续历史。
- **个人维护量级**：新增能力不能引入需要长期运维的基础设施。现有基础设施只有 GitHub Actions、Cloudflare Pages + Functions、本地机器。
- **结论先行**：每页第一屏给判断（状态、评分、报警、一句话），数据与图表在下层，两层互相联动。

写法约定：

- 自然语言用中文；identifier 与国际通用术语（VIX / SKEW / GEX / Wheel / CSP）保留英文原名。
- 页面时效统一写「数据截至 …」。

## Brand Commitments

- 站名「观澜台」。仓库名 `market-base` 保持不变。
- 全站免责声明：内容不构成投资建议。
- 没有 logo 以外的品牌资产、没有客户名、没有价格信息。
- 语气与现有页面一致：陈述读数与判断，标注不确定处，不写营销腔。

## Evidence on Hand

- 线上站点 https://market-base.pages.dev ，由真实历史数据驱动。
- `data/` 约 789 MB 真实抓取历史，宏观序列最长回溯到 1854 年，SEC / Fed 原文近 2 年。
- 领域词汇表 `CONTEXT.md`、架构决策记录 `docs/adr/`、工程约定 `AGENTS.md`。

不存在的证据：没有任何使用者反馈、用户量统计、付费记录、客户案例或媒体报道。以后的工作不得编造这些。

## Product Principles

1. 先给结论，再给依据；一个没有依据的结论算未完成。
2. 口径可追溯胜过覆盖广度：一个标了层级的读数，比十个来源不明的读数更有用。
3. 判断要能被后来的自己复核，所以观测日、快照口径与判断依据要留在页面上。
4. 运维成本算在功能成本里：新能力要连带它每天花掉的时间与注意力一起衡量。

## Accessibility & Inclusion

全站是自用工具，没有特定残障用户需求记录。已经执行的通用门槛：

- 文字对比度按 WCAG AA 分两档——图形填充色与文字色分开取令牌。
- 触控目标不低于 24 CSS px（WCAG 2.2）。
- ECharts 图的无障碍描述改成小节标题加系列名，不朗读原始数据点。
