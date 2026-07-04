# 项目领域模型 (CONTEXT.md)

本文档是 K线分析项目的领域术语表（ubiquitous language）。只记录领域概念，不含实现细节。实现决策见 `docs/adr/`。

## TUI

- **技术分析模式**：TUI 两种模式之一。单标的 K 线图 + 指标副图 + 评分诊断侧栏。画图吃 `compute_all_indicators(df)` 的整条 df，侧栏吃 `analyze()` 返回的 dict。
- **宏观模式**：TUI 两种模式之一。多系列时序折线 + 期限结构快照。覆盖 FRED 9 分类 + 流动性派生指标。不与 K 线混排，独立视图。
- **回看（lookback）**：光标移到任意历史 K 线，侧栏显示那天的完整诊断（指标数值 + 评分信号）。区别于"最新快照"（仅看 `iloc[-1]`）。回看要求所有判断无未来函数。

## 指标

- **派生指标（derived metric）**：由两个或多个原始系列运算而成，非 CSV 现成列。如 2s10s 利差（DGS10−DGS2）、净流动性（WALCL−RRP−TGA）、BEI（DGS5−DFII5）。定义集中于 `code/macro.py`，不进 fetchers 也不进 `indicators.py`。
- **期限结构（term structure）**：某一时点、按期限（1mo→30y）排列的收益率曲线。仅对 rates/tips 分类有意义。
- **时序折线**：以时间为 x 轴的单/多系列折线图。派生指标作为可选系列并入此类，不单独成图。
