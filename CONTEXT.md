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

## 前端

- **主站 SPA**：static/index.html 单页壳 + 四视图（市场仪表盘 / 技术分析 / 宏观 / 关联分析）。全站交互最重的部分（symbol 切换、tab 状态、图表联动），保持 vanilla JS，不迁 Astro。
- **专题页**：挂在主站导航下的 32 个静态内容页（rates / credit / liquidity / assets / fed / treasury…），静态壳 + ECharts 图表 + 规则引擎叙事。样板重复痛点的所在，Astro 迁移的唯一对象。
- **island**：专题页里的交互孤岛（ECharts 图表组件）。页面其余部分为零 JS 静态 HTML，由 Layout 统一渲染 head / 导航 / 样板。
- **预渲染数据**：export_pages.py 构建期调分析层路由产出的静态 JSON（与本地 FastAPI 的 /api/* 同路径）。页面运行时 fetch 它——数据日更与代码改动解耦的关键机制，迁移不触碰。
