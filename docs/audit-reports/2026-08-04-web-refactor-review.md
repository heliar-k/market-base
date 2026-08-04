# Web 重构（timsun 复刻）审查报告 — 共识终版

- 日期：2026-08-04
- 审查范围：`static/rates/`（fed-funds / yield-curve / auctions）、`static/credit/`（含 stress）、`static/volatility/`、`static/js/rates-common.js`、`static/js/liquidity-heatmap.js`、`src/rates_analysis.py`、`src/credit_analysis.py`、`src/fed_analysis.py`（_score_text）、`src/server.py`、`src/fetchers/treasury_fetcher.py`
- 方法：双 reviewer 独立审查 → adversarial review（逐条投票）→ 共识
- 结论：提交审查的发现中 **15 条幸存**（其中 14 条 2/2 票通过，1 条 1/2 票保留），**0 条被否决丢弃**。全部幸存发现均有代码/数据实证，无一条是误报。

---

## 幸存发现清单（按优先级排序）

### P1-① fed-funds.html SOFR 成交量单位换算错误 10 倍
- **位置**：`static/rates/fed-funds.html`
- **问题**：`Math.round(l.sofr_vol.value / 1000)} 千亿` —— FRED SOFRVOL 单位是十亿美元（本地最新 3205 = $3205B），3205/1000=3 → 页面显示"3 千亿"，实际应为 32.05 千亿（或 3.2 万亿）。正确换算应为 /100（千亿）或改单位标签为万亿。
- **佐证**：`src/server.py get_rates_fed_funds` 原样透传 SOFRVOL 无换算，问题出在前端换算。
- **幸存理由**：算术错误可直接复现，数值差 10 倍，属展示层数据错误。

### P1-② fed-funds 页 EFFR 用月度序列按日频绘制，图表错位
- **位置**：`static/rates/fed-funds.html` + `data/fred/rates/rates.csv`
- **问题**：FEDFUNDS 是月度均值（865 个观测，1954-07→2026-07-01，日期均为每月 1 号；日频应为 DFF），但页面当日频处理：(1) "EFFR 5 年"图实际画出 1954→2026 共 72 年 865 个点（API 实测 effr_history len=865，first=1954-07-01）；(2) 走廊图 x 轴取 SOFR 的 90 个日频日期，而 effr / target_lo / target_hi 三序列日期范围各不相同，前端按位置对齐 → EFFR 线整体右移 ~30 天、目标线左移 ~46 天，三线全部错位。
- **幸存理由**：API 实测长度与首末日期佐证充分；多序列图按位置而非日期轴对齐是确定的设计缺陷。

### P1-③ yield-curve.html 全球长端对照表"来源"列显示错误数据
- **位置**：`static/rates/yield-curve.html` + `static/js/rates-common.js`
- **问题**：页面传 6 列头 `['市场','说明','10Y 收益率','30Y 收益率','vs 美国 (bp)','来源']`，但行对象键为 `{market,note,rate,rate30,spread,spread30,source}` 共 7 个；`R.table` 按 `Object.keys(row)[j]` 取列 → 第 6 列"来源"实际渲染的是 `spread30`（30Y vs 美国利差），`source` 字段（'FRED DGS10 · daily' 等）永远不显示。
- **幸存理由**：键序 vs 列头静态分析即证，用户可见的错误列。

### P1-④ 全球长端 spread 符号约定与 timsun 相反
- **位置**：`src/rates_analysis.py global_long_end`
- **问题**：`spread_vs_us = JP10Y − DGS10 = 2.67−4.75 = −208bp`；timsun.net 同数据（JP 2.670%、US 4.700%）显示 **+203.0 bp（= US − JP）**。本地表头"vs 美国 (bp)"下显示 −208 语义自洽，但与参考站符号约定相反，对比阅读易误解。
- **幸存理由**：与参考站同源数据的符号差异可直接对照证实。

### P1-⑤ global_long_end 的 `or 0` 兜底产生假利差
- **位置**：`src/rates_analysis.py`
- **问题**：spread 计算用 `(_snapshot(...) or 0) - DGS10`：当 JP10Y/cgb 列存在但全为 NaN 时（如数据未拉全），spread_vs_us 显示为 −DGS10×100 的假值（如 −475bp）而非"—"；只有整列缺失才返回 None。
- **幸存理由**：NaN 全列 ≠ 缺失列，兜底语义错误，会在数据缺口时静默展示假利差。

### P1-⑥ rates_analysis 联储利率预期验证项判据与自身熊陡叙事矛盾
- **位置**：`src/rates_analysis.py yield_curve_analysis`
- **问题**：`ok = y2 < effr`（2Y 低于 EFFR = 定价降息 = 与熊陡前提一致），当前数据 EFFR 3.63% vs 2Y 4.28% → 标 ✗"短端定价加息，与熊陡前提矛盾"，但同页 driver 归因是"实际利率/期限溢价主导"——长端驱动的熊陡本就伴随 2Y>EFFR，判据不成立。timsun 同场景写的是"✓ 降息预期回落，2Y 在 4.25% 处获得支撑"。
- **幸存理由**：判据逻辑与同页归因自相矛盾，且与参考站处理相反，会在收益率曲线页制造误导性"确认度下降"。

### P1-⑦ 拍卖页 10Y/30Y 趋势图漏掉重开拍卖，曲线严重不完整
- **位置**：`src/server.py get_rates_auctions`
- **问题**：按 `security_term == '10-Year'/'30-Year'` 精确匹配，但重开标的 term 是 "9-Year 11-Month" / "29-Year 10-Month" 等（`data/treasury/auction_results.csv` 实测：'10-Year' 222 条 vs '9-Year*' 272 条；'30-Year' 139 条 vs '29-Year*' 195 条）。近 365 天窗口内 10Y/30Y 各只剩 ~4 个原始发行点，而 2Y/5Y（从不重开）不受影响——4 条线口径不一致。
- **幸存理由**：CSV 实测计数差异显著，重开拍卖被系统性漏掉，趋势图严重失真。

### P1-⑧ credit 分位计算用 `<=` 包含并列值，虚高 5~18pp
- **位置**：`src/credit_analysis.py _pct`
- **问题**：`(tail <= cur).mean()`，FRED OAS 为整 bp 离散数据，当前值并列极多（IG_OAS 在 0.80 处并列 46 条）。本地实测 IG：1Y 68.0/3Y 29.2/10Y 27.8（<=）vs 58.8/23.1/22.0（严格 <）；timsun.net/credit 显示 58%/22%/20%，与严格小于口径吻合。连锁影响：overview 卡片分位、Regime Score 的 spread_level（本地 27.4 vs timsun 21.6）、stress 分量、`_rolling_pct` 历史曲线。
- **幸存理由**：本地实测与参考站数值直接对拍，偏差 10pp 级，且辐射多个下游指标。

### P1-⑨ credit stress 分位窗口与当前 timsun 不一致（v1 vs v2）
- **位置**：`src/credit_analysis.py STRESS_W=250`
- **问题**：注释称"对齐 timsun 365 天"，但当前 timsun.net/credit/stress 为 v2："压力指数采用最长 10 年滚动历史（3650 天），样本不足时按实际可用历史计算"。同快照（2026-07-30）对比：本地 composite=38.3（HY 52.8、IG 68.0、VIX 23.6）vs timsun composite=24（HY 23、IG 20、VIX 51）。页面 stress.html 自己也标注"对齐 timsun v1"——与参考站现状脱节，分区判定可能从"宽松"变成"中性"。
- **幸存理由**：同快照对拍差异巨大（38.3 vs 24），窗口口径已确认脱节。

### P1-⑩ credit 依赖的 yfinance 资产快照仅 5 行且无 volume 列
- **位置**：`data/yfinance/asset_prices.csv`
- **问题**：实测 5 行（2026-07-31→08-04）、无任何 *_volume 列 → 信用页 Regime Score 的 Market Liquidity 子分缺失（HYG/LQD 22 日动量需 ≥23 条），综合分 44.9 由 6/7 子分降级计算（timsun 46.0 含 Market Liquidity 58.8）；CDS 页 bank 偏离退化为 1 日窗口（KBWB 仅 2 个非空值）；commit 声称"HYG/LQD 流动性卡片补成交量"但实际数据无 volume 列（save_daily_csv 有 dp.volume 写列逻辑，近期拉取 volume 为 None）。
- **幸存理由**：数据文件实测 + 代码降级路径双重佐证；属数据状态问题但会静默改变 Regime Score 口径。

### P1-⑪ 流动性仪表盘堆叠面积图严重造假：fillna(0) 造成锯齿
- **位置**：`src/server.py get_liquidity_overview`
- **问题**：stacked 累积用 `running.add(col.fillna(0))`，而 WRESBAL/WTREGEN 是周频（最近 20 行中 16 行 NaN）、RRPONTSYD 日频 → 非周三日期累计从 ~$3.9T 跌到 ~$0。实测模拟：fillna(0) 的顶层序列 07-30/07-31/08-03 为 0.0 而 ffill 应为 3895.3（百万美元），最大假跌落 3.9 万亿美元。该 stacked 被 `static/js/liquidity-heatmap.js renderStackedArea` 直接消费，图表呈梳齿状假象。
- **幸存理由**：模拟复现 + 消费方直接引用，图表造假幅度达万亿级，属最严重展示错误之一。

### P1-⑫ fed_analysis 关键词短语子串重叠导致双重计分
- **位置**：`src/fed_analysis.py _score_text`
- **问题**：对每个短语独立 `words in text` 命中即加分，无最长匹配：'rate cuts' 同时命中 'rate cuts'(−3) 与 'rate cut'(−3) → −6（clamp 到 −5，任何含 rate cuts 的句子直接判极鸽）；'further tightening' → +3.5；'policy easing' → −3.0；'remain patient' → −0.5。实测：`_score_text('The committee expects rate cuts later this year.') = −5.0`（应为 −3）。
- **幸存理由**：可复现的计分偏差，直接影响鹰鸽追踪页评分与时间线。

### P2-① 拍卖 tail_bp 为代理口径，非标准 tail
- **位置**：`src/fetchers/treasury_fetcher.py`
- **问题**：`tail_bp = (high_yield − avg_med_yield)×100`；市场标准的 "auction tail" 是 high yield − when-issued yield，而 avg_med_yield（中标收益率中位数的均值）≠ 发行前 WI 收益率。作为需求疲软指标方向大致可用，但与 timsun/新闻通稿的 tail 口径不同，页面未标注。
- **幸存理由**：口径差异属实（1/2 票保留）；不构成数值错误，但页面应标注代理口径。

### P2-② 波动率卡片变动口径与 timsun 不同
- **位置**：`static/volatility/` vix_card
- **问题**：本地用百分比（chg_1w_pct=-15.05%），timsun VIX 卡片用点数（"7 日变化 −2.84pt"，15.86−18.70）；信号正文两站一致用百分比。口径差异非错误，但卡片与参考站不可直接对比。
- **幸存理由**：事实差异确认，属口径一致性建议而非错误。

---

## 修复建议优先级

1. **P1 优先**：P1-⑪ fillna(0)→ffill（1 行改动，消除万亿级假象）；P1-① 单位换算 /100；P1-⑦ startswith 匹配重开拍卖；P1-⑧ `<=`→`<` 分位。
2. **P1 次之**：P1-③④⑤⑥ 表头/符号/兜底/判据修正（均为 rates_analysis / server 小改动）；P1-⑨ STRESS_W 对齐 v2（3650 天）；P1-② 换 DFF + 日期轴对齐；P1-⑩ 数据侧补 volume（非代码）；P1-⑫ 短语最长匹配。
3. **P2**：P2-① tail 口径标注；P2-② 卡片口径与参考站对齐或标注。

> 备注：共识阶段输入仅含幸存发现（15 条），其中 14 条 2/2 票、1 条（P2-①）1/2 票保留；本阶段未收到被否决发现的明细，故无法报告具体被丢弃条数——按输入推断为 0 条被否决。
