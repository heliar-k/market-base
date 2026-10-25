# 项目领域模型 (CONTEXT.md)

本文档是 market-base 项目的领域术语表（ubiquitous language）。只记录领域概念，不含实现细节。实现决策见 `docs/adr/`。

## TUI

- **技术分析模式**：TUI 两种模式之一。单标的 K 线图 + 指标副图 + 评分诊断侧栏。画图吃 `compute_all_indicators(df)` 的整条 df，侧栏吃 `analyze()` 返回的 dict。
- **宏观模式**：TUI 两种模式之一。多系列时序折线 + 期限结构快照。覆盖 FRED 9 分类 + 流动性派生指标。不与 K 线混排，独立视图。
- **回看（lookback）**：光标移到任意历史 K 线，侧栏显示那天的完整诊断（指标数值 + 评分信号）。区别于"最新快照"（仅看 `iloc[-1]`）。回看要求所有判断无未来函数。

## 指标

- **派生指标（derived metric）**：由两个或多个原始系列运算而成，非 CSV 现成列。如 2s10s 利差（DGS10−DGS2）、净流动性（WALCL−RRP−TGA）、BEI（DGS5−DFII5）。定义集中于 `src/macro.py`，不进 fetchers 也不进 `indicators.py`。
- **期限结构（term structure）**：某一时点、按期限（1mo→30y）排列的收益率曲线。仅对 rates/tips 分类有意义。
- **时序折线**：以时间为 x 轴的单/多系列折线图。派生指标作为可选系列并入此类，不单独成图。
- **欧元区主权利差（euro sovereign spread）**：各国 10Y 国债收益率 − 德国 Bund 10Y，日频 bp，按国家各成一条线（法 / 意 / 西）。信用域唯一一条数源不在 FRED 的读数——欧洲官方体系没有分国日频 10Y（Eurostat / ECB 只发布欧元区聚合曲线，各国央行只给月频），故走 CNBC 市场报价，存 `data/euro_sovereign/yields.csv`；其月度均值与 FRED/BDE 官方月频经交叉校验一致（≤2bp），区别只在发布滞后。定义在 `src/credit_analysis.py::euro_sovereign()`，呈现于 credit 页「欧洲主权利差」段。

## 共识层

- **共识卡（consensus card）**：多成员投票合成的两级状态卡（市场风险轴 / 宏观条件轴），单源在 `src/consensus.py`（ADR-0004）。状态只有四种：确认 on / 确认 off / 分歧 / 数据不足；「分歧」= 票面相持或未达确认线，不另设中性态。
- **成员（member）**：进投票池的一个数据面（如趋势、广度、HY OAS）。内部可含多个子信号；缺数据或票龄超限 → 缺席，剔除出分母。
- **子信号（sub-signal）**：成员内最小判定单元，独立产出 on/off/弃权；成员票 = 子信号多数，平票弃权。
- **票龄（vote age）**：成员数据观测日距今的工作日数；> 2 个工作日按缺席处理（宁漏报不误报，节假日使其略宽松）。
- **确认阈值（confirm bar）**：参与票同向 ≥ 2/3 才给「确认」，否则「分歧」；参与票不足半数 → 「数据不足」。

## 前端

- **主站 SPA**：frontend/public/app/index.html 单页壳（ADR-0005 后从站点根挪到 /app/，/ 整页重定向到今日头版）+ 两视图（技术分析 / 关联分析）。全站交互最重的部分（symbol 切换、tab 状态、图表联动），保持 vanilla JS，不迁 Astro。
- **今日头版**：唯一的「今日」页 = /daily/（SITE_NAV home，ADR-0005）。单列纵排「结论 → 叙事 → 数据 → 导航」：头条区 → 今日一句话 → 情景卡与跨资产变化表 → 市场快照与波动率 → 专题入口。
- **头条区（hero）**：/daily/ 首屏结论锚点：共识完整卡 + 主导情景徽章 + 结构报警 + FOMC 倒计时 + 衰退概率。全站唯一渲染地，不在别处重复（五项重叠双维护的历史包袱，ADR-0005 清偿）。
- **市场快照**：六板块（股/债/商品/ETF/加密/外汇）当日涨跌，行情视角，挂专题链接。与跨资产变化表共存并分工：跨资产表 = 研判视角（Δ5/Δ20 + 正反面 + 时效），快照 = 行情视角。
- **自选清单（watchlist）**：用户盯盘的个人标的集合（localStorage）。家住技术分析视图侧栏，与 symbol-list 同组；点行无刷新切图（go-stock），✕ 显式删除。
- **专题页**：挂在主站导航下的 32 个静态内容页（rates / credit / liquidity / assets / fed / treasury…），frontend/src/pages/ 下的 .astro 源 + ECharts 图表 + 规则引擎叙事。样板重复痛点的所在，Astro 迁移的唯一对象（已全部迁完，工单 #18）。
- **island**：专题页里的交互孤岛（ECharts 图表组件）。页面其余部分为零 JS 静态 HTML，由 Layout 统一渲染 head / 导航 / 样板。
- **预渲染数据**：export_pages.py 构建期调分析层路由产出的静态 JSON（与本地 FastAPI 的 /api/* 同路径）。页面运行时 fetch 它——数据日更与代码改动解耦的关键机制，迁移不触碰。
- **伴读抽屉（companion drawer）**：全站共享（专题页 + 主站 SPA）的右侧可折叠 LLM 问答面板。携带本页时效、结论摘要与用户划词的引用胶囊发起对话；答案按 Markdown 渲染（标题 / 有序无序列表 / 表格 / 围栏代码块 / 引用，单源 frontend/public/js/ai-md.js）；已发出的提问可「编辑」——该条及之后作废、文本与引用回填重发，末条答案可「重试」按当前设置重来；会话跨页延续、持久化于本地存储，并支持导出为结构化 Markdown 研报笔记。
- **本站代理（site proxy）**：伴读抽屉的免 Key 通道。Base URL 保持默认且未填 Key 时请求走本站 `/api/chat`，服务端用 `LLM_API_KEY` 转发上游、按白名单选模型并限流；填了 Key 或改 Base URL 即直连（BYOK）。本地实现是 `src/server.py`，线上是 `functions/api/*.js`（Cloudflare Pages Functions），契约见 `docs/ai-chat-backend.md`。
- **笔记落盘（note save）**：抽屉「存笔记」把整条对话（含引用块）落成 Markdown。本地 `data/notes/`；线上绑 `NOTES` KV 时落 KV，未绑返回 503 并提示导出 `.md`——静态站没有 `data/` 写权限。
- **模型探测（model probe）**：伴读抽屉设置里的按钮，GET `{BaseURL}/models` 列出网关模型（magpie 的 65 个带 display_name），并按所选模型的 reasoning_levels 填充「推理强度」，随请求以 `reasoning_effort` 透传（网关不认就忽略）。Base URL 保持默认且无 Key 时才回落本站 `/api/chat` 代理位；改成别的地址即直连——本地网关多半不校验 key。
- **引用胶囊（quote chip）**：用户在页面正文或表格划词后注入伴读抽屉的上下文片段。除选中文本外，自动锚定所属页面、数据时效、所在小节标题及表头行，支持跨小节/跨页叠加多枚后一并提问。
- **伴读工具（drawer tool）**：伴读抽屉里模型可主动调用的只读取数通道，把站内预渲染端点包装成 LLM function tools，补齐「页面摘要里没有、但站内已导出」的读数（其他页面、更长历史、其他标的）。三个工具三种形态：`query_site_data` 是**具名端点枚举**（17 个「一个端点一个读数包」的端点，模型只能选不能拼路径）；`query_correlation`（22 标的 30 日相关矩阵）与 `query_options_wall`（22 个标的的期权快照）是**固定端点 + 带标的参数**的跨端点工具，在客户端把原始大包整形成可引用的读数——前者解决「矩阵里没有名字」（484 个裸浮点连不上标的），后者解决「结论读数埋在绘图明细下面」（单标的九成是逐行权价与分布曲线）。执行在浏览器（同源 `/api/*`），不是服务端；工具往返只在内存里跑，不进 `thread.messages`。边界见 ADR-0006，切片见 `docs/SPEC-ai-drawer-tools.md`。

## 交易策略工具

- **确定性巨头池（Wheel pool）**：用于期权车轮策略（Sell Put 抄底收租 + Covered Call 波段止盈）的 17 只宽护城河/强现金流美股标的池，单源在 `src/wheel_scanner.py`，按单手占压资金与波动率甜点分为 T0（核心主力）、T1（跨行业寡头对冲）、T2（单手过重或低波薄利的观察池）三档；扫描结果内嵌于 `src/assets_analysis.py::options_board()` 的 `wheel` 字段，挂载在网页端期权结构页 `/assets/equities/options/`。
- **事件排雷（event alert）**：期权快照顺手采集的下次财报日（`earnings_date`）与除息日（`ex_dividend_date`）倒计时。财报 ≤ 7 天强制拦截裸卖近月信号（防跳空穿仓或卖飞底仓），8~35 天标注跨财报预警，除息 ≤ 14 天标注 Sell Call 提前行权风险。
