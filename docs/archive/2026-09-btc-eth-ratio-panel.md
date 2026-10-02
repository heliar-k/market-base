# Task Plan — BTC/ETH 价比分析面板【已归档】

> **归档说明（2026-10-02 文档卫生）**：本计划已全部落地，功能已上线。
> - 提交：A 阶段 d047f1be、B/C 阶段 91248c43（2026-09-28）
> - 分析层证据：`src/assets_analysis.py` — `_ratio_block`（L1787，含 MA50/乖离/RSI(14)/叙事，即 A 阶段）、`_funding_diff_series`（L1375）、`_ratio_corr`（L1428）、`_ratio_nl_beta`（L1464）、BTC Dominance（L1607）、`_etf_flow_ratio`（L1643）
> - 前端证据：`frontend/src/pages/assets/crypto.astro` — `<Section id="ratio" title="BTC/ETH 价比" />`（L18）、价比渲染块（L163-238，含 ETH/BTC 倒数轴图、指标卡、funding 差、规则引擎叙事）
> - 原文各阶段标题的 ✅ 为准；子项 checkbox 未勾是当时的记录习惯，不代表未完成。
> - 原文件位于根目录 `task_plan.md`，被 `.gitignore` 命中（pi planning-with-files 会话草稿，不入库），故重命名归档于此。
> - 注意：文中 `crypto.html` / `src/assets_analysis.py:1394` 等路径行号为迁移前口径，现状见上。

---

> 来源：2026-09-28 会话讨论。等主 session（crypto-derivatives 页修复/优化）完成后再动手。

## 背景

`/assets/crypto` 页目前把 BTC/ETH 价比只画成归一化走势图的右轴虚线
（`src/assets_analysis.py:1394` 算出 RATIO 序列后无任何分析）。
数据核实：`data/yfinance/asset_prices.csv` BTC/ETH 日线 767 行（2024-08-22 起），
当前价比 31.19，区间 23.02~55.40，1y z-score -0.41，ratio/MA50 0.976。

## 目标

给价比加「指标层 + 交叉验证层 + 规则引擎结论」，全部用现成数据，零新 fetcher。

## 任务

### A. 价比自身指标（`src/assets_analysis.py` 的 `crypto()` 加 `ratio` 块）✅ 2026-09-28 完成（d047f1be）

- [ ] MA20/50 均线带 + 乖离率（ratio/MA50）
- [ ] 1 年滚动 z-score + 百分位（与 LAYER1 KPI 卡同口径）
- [ ] RSI(14) on ratio
- [ ] 7d/30d 动量（30d 动量转正 = 风险偏好外移/山寨季判定）
- [ ] 前端：`crypto.html` 加独立价比图（建议改画 **ETH/BTC 倒数轴**，
      上行=ETH 强，符合山寨季直觉），下方配指标卡
- [ ] 规则引擎叙事框架：价比下行=风险偏好外移（牛市中后段特征）；
      价比上行=加密内部避险回流 BTC（回调期典型）

### B. 交叉分析 — 全套 ✅ 2026-09-28 完成（91248c43）

- [ ] **funding 差卡**（最高优先）：`crypto_derivatives.json` 的
      `perp.BTC.funding_annual` vs `perp.ETH.funding_annual`。
      轮动质量判定：价比动量↑ + ETH funding 相对走阔 = 杠杆推动（脆弱）；
      funding 差平稳 = 现货驱动（健康）。
      当前实况：BTC 年化 +6.2% / ETH -1.6%（无人杠杆追 ETH）。
- [ ] PCR 差：`options_BTC.pcr` vs `options_ETH.pcr`（现 0.53/0.55），
      期权市场相对保护需求。
- [ ] BTC-ETH 30d 相关性：`data/cross_asset/correlation.csv` 已有。
      相关性高位+价比横盘=无轮动；相关性回落+价比动量=轮动确认。
- [ ] 价比 × 净流动性脉冲：复用 crypto 页 NL 序列 + `_reg_beta` 框架；
      ETH 是高 β，NL 扩张先 BTC 后 ETH，价比滞后触底=外溢确认。

### C. 新数据源 ✅ 2026-09-28 完成（91248c43；BTCD 用 CoinGecko /global，
  ETH ETF 流用 Farside ETH 页，Polymarket 用现有年终阶梯；均注册进 daily-fetch）

- BTC Dominance（需 CoinGecko/Coinglass 新源）
- ETH 现货 ETF 资金流（Farside ETH 页，复用 `etf_flows_fetcher` 模式；
  "ETH ETF 流量/BTC ETF 流量"比值与价比共振是强确认）
- Polymarket 隐含涨幅比（两标的对赌阶梯已在 crypto-derivatives 页）

## 体量估计

A 全套 + B(1)：分析层 ~50 行 Python + 前端一张图 + 一条结论，单次提交。

## 前置依赖

- 主 session 的 crypto-derivatives 页工作先合并（避免 `assets_analysis.py`
  同区域冲突；价比块挂在 `crypto()`，理论冲突面小）。
