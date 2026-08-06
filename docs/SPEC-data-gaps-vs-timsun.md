# Spec：数据补齐一期 — 对齐 timsun.net 资产/波动率/持仓数据

## Problem Statement

用户以 timsun.net（美国宏观研究平台）为对标，已复刻 `/rates`、`/fed`、`/volatility` 三个板块的 Web 页面，但数据层仍存在明显缺口：资产价格快照缺 8 个标的（债券/商品/加密/外汇/半导体 ETF），CBOE 波动率指数只有 9 个（对方面板 30 个），Nasdaq 100 分析师目标价、跨资产相关性、市场广度等派生指标完全没有。逐一对照 timsun 全站 16 个板块 40+ 子页后，缺口收敛为三类：资产快照小补丁（纯加列）、现有数据域扩展（波动率/COT/目标价/相关性）、全新数据域（加密衍生品/ETF 看板，工作量最大，另行评估）。

## Solution

一期补齐数据层（不涉及 Web 页面复刻），让本地数据覆盖 timsun 资产、波动率、持仓、目标价四个板块的数据需求：

1. **资产快照补 8 标的**：IEF、NG、ETH、EURUSD、GBPUSD、USDKRW、SMH、SOXX 加入 yfinance 快照管线，`data/yfinance/asset_prices.csv` 自动扩列。
2. **CBOE 波动率指数扩到全家族**：新增 ~20 个指数历史数据，与现有 9 个合并成完整 30 指数集，覆盖商品/股指/期限/尾部/新兴市场/个股波动率。
3. **Nasdaq 100 分析师目标价**：新增成分股目标价快照（现价/平均目标/最高/最低/隐含空间/分析师数/评级），支持 Top10 上行/下行/分歧榜单与行业聚合。
4. **跨资产相关性矩阵**：基于资产快照计算 30 日滚动相关性，含股债相关、油股相关两个关键报警项。
5. **COT 扩展**：持仓追踪从 9 个品种扩展到 VIX 期货、5Y/10Y/30Y 美债、EUR/JPY/DXY。

## User Stories

1. 作为用户，我希望资产快照包含 IEF/NG/ETH/EURUSD/GBPUSD/USDKRW/SMH/SOXX，以便资产仪表盘六个板块（美股/债券/商品/ETF/外汇/加密）与 timsun 完全对齐。
2. 作为用户，我希望波动率数据包含 GVZ/VXSL/VXNG（商品）、VXN/VXD（股指）、VVIX/VXV/VXMT/VXMO/VIN/VIF/VXTH（期限与尾部）、VTLT/VXHY（债券）、VEWZ/VEEM/VXEF（新兴市场）、VXAP/VXAZ/VXGO/VXGS/VXIB（个股）共 ~20 个新增指数，以便波动率全景面板与 timsun 30 指数热力图对齐。
3. 作为用户，我希望 Nasdaq 100 每只成分股有分析师目标价快照（平均/最高/最低/分析师数），以便生成上行空间 Top10、下行风险 Top10、目标价分歧 Top10 榜单。
- 4. 作为用户，我希望目标价按行业聚合（覆盖数、平均/中位空间、平均分析师数），以便快速判断行业层面的预期差。
5. 作为用户，我希望得到跨资产 30 日滚动相关性矩阵，以便看到 SPX-TLT（股债）、WTI-SPX（油股）等关键相关性的实时状态与报警。
6. 作为用户，我希望 COT 持仓覆盖 VIX 期货、美债 5Y/10Y/30Y、EUR/JPY/DXY，以便持仓追踪页覆盖股指/波动率/利率/外汇/商品全部类别。
7. 作为用户，我希望以上所有新增数据遵循现有 CSV 存储约定（date 首列、ISO 格式、观测日 upsert），以便现有分析层与 Web 层零改动读取。
8. 作为用户，我希望新增数据由 GitHub Actions 每日自动拉取并 commit，以便本地 `git pull` 即得。

## Implementation Decisions

### 测试缝（Seams）

复用现有 fetcher 测试缝，不新增：每个 fetcher 以「返回 DataFrame / 写 CSV」为外部契约，测试用 `tmp_path` 隔离 + autouse 清缓存 + mock 网络（先例：`test_cot_fetcher.py`、`test_barchart_futures_fetcher.py`）。派生计算（相关性矩阵）走现有纯函数测试模式（先例：`test_macro.py`）。全项目一条缝：fetcher 层 + 派生层各一个测试点，不引入新抽象。

### 1. 资产快照扩展（YF_TICKERS）

- `src/config.py` 的 `YF_TICKERS` 新增 8 键：`IEF`（IEF）、`NG`（NG=F）、`ETH`（ETH-USD）、`EURUSD`（EURUSD=X）、`GBPUSD`（GBPUSD=X）、`USDKRW`（KRW=X）、`SMH`（SMH）、`SOXX`（SOXX）。
- yfinance 快照 fetcher 无逻辑改动（快照管线按配置自动扩列），`data/yfinance/asset_prices.csv` 新增 8 列。
- 同步更新 `docs/DATA_CATALOG.md`。

### 2. CBOE 波动率指数扩展

- 扩展现有 CBOE fetcher 的系列清单（含代码映射），新增指数全部走 CBOE 官方 CSV 下载：`https://cdn.cboe.com/api/global/us_indices/daily_prices/{CODE}_History.csv`（现有 VIX 系列同源，已验证可用，无需新依赖）。
- 新增指数清单（20 个）：`GVZ VXSL VXNG VXN VXD VVIX VXV VXMT VXMO VIN VIF VXTH VTLT VXHY VEWZ VEEM VXEF VXGO VXGS VXIB VXAP VXAZ`（22 个候选，部分若 CBOE 无历史数据则跳过并在日志注明）。
- 存储：沿用 `data/cboe/volatility.csv` 宽表模式（每指数一列），观测日 upsert。全量历史一次拉取后每日增量。
- 已知限制：VIN/VIF 等极新指数历史可能较短（不足 1 年），接受现状。

### 3. Nasdaq 100 分析师目标价

- 新增 fetcher：成分股列表来自 `^NDX`（yfinance 返回的 components），逐票拉 `Ticker.info` 的 `targetMeanPrice / targetHighPrice / targetLowPrice / numberOfAnalystOpinions / recommendationKey / industry / sector`。
- 存储：`data/analyst/ndx_targets.csv` 观测日快照（宽表，一行一票），每次全量覆盖当天快照 + 保留历史日（按日期追加）。
- 单次拉取约 100 票 × 1 次 info 调用，可接受（Actions 每日一次，串行 + 短 sleep）。
- 失败票（info 缺字段/网络错）记入日志跳过，不阻塞整批。

### 4. 跨资产相关性矩阵

- 派生计算模块（复用 `src/macro.py` 模式）：读 `data/yfinance/asset_prices.csv`，对关键标的（SPX/NDX/RUT/DJI/TLT/HYG/LQD/Gold/Silver/WTI/Copper/BTC/DXY 等）计算 30 日滚动日收益率相关系数矩阵。
- 输出 `data/cross_asset/correlation.csv`（观测日 + 完整矩阵宽表），另输出两个报警标量：`SPX_TLT_30d`（股债相关）、`WTI_SPX_30d`（油股相关）。
- 由 Actions daily-fetch 在快照更新后触发；纯本地计算，无新数据源。

### 5. COT 扩展

- 现有 COT fetcher 扩展系列清单：新增 VIX 期货、美债 5Y/10Y/30Y、EUR/JPY/DXY（CFTC disaggregated/TFF 报告已有这些合约，无需新源）。
- 存储沿用 `data/cot/cot.csv` 宽表模式，列名按现有 `{ROOT}_OI / {ROOT}_DEALER_L/S / {ROOT}_ASSET_L/S / {ROOT}_HEDGE_L/S` 约定扩展。
- 无 VIX 期货的 disaggregated 分类时降级为 TFF（Traders in Financial Futures）口径，与现有 ZQ 处理一致。

## Testing Decisions

- 好测试的标准：只测外部契约（fetcher 返回的 DataFrame 列名/行数/upsert 行为、派生函数的输出形状与数值边界），不测内部实现细节。
- 资产快照扩展：配置测试断言 `YF_TICKERS` 含新增 8 键；快照管线测试断言扩列后 CSV 列完整（先例：`test_config.py`、`test_io.py`）。
- CBOE 扩展：mock HTTP 响应（CBOE CSV 样例），断言新指数列正确解析、缺失历史不崩溃（先例：`test_barchart_futures_fetcher.py`）。
- 目标价 fetcher：mock `Ticker.info`（含/缺目标价两种票），断言快照 CSV 行完整、失败票跳过（先例：`test_cot_fetcher.py` 的 mock 模式）。
- 相关性矩阵：构造已知相关性的人工输入，断言矩阵数值与报警阈值正确（先例：`test_macro.py`）。
- COT 扩展：mock CFTC 响应，断言新合约列解析正确。

## Out of Scope

- **加密衍生品雷达**（BTC ETF 资金流 / CME 基差 / Coinglass 资金费）：独立数据域，工作量最大，二期另行 spec。
- **ETF 全量看板**（~3000 只清单 / AI 纯度评分 / 持仓穿透）：二期另行 spec。
- **市场广度 ABV200**（SPX 500 成分股日线）：需 500 只标的每日全量拉取，数据量级独立于本期，二期评估。
- **经济意外指数**（Surprise Index）：Citi 系列 FRED 已停更，替代数据源可行性未验证，单独立项。
- **内容型板块**：新闻流 / 每日研判归档 / 复盘账本 / 基金信函 / 研报库 / 半导体资讯 / AI 产业热力图 —— 非数据管道，另行评估。
- **Web 页面复刻**：本期只补数据层；`/assets` 等页面渲染待数据到位后另起 Web spec。

## Further Notes

- 数据源全部免费匿名：CBOE cdn CSV（波动率）、yfinance（资产/目标价/成分股）、CFTC 官方（COT）。
- 一期完成后，timsun 的 `assets` 六板块价格、波动率 30 指数、持仓追踪、目标价四个面板的数据需求全部对齐。
- 二期候选（按工作量升序）：COT 之外的市场广度 → 加密衍生品 → ETF 看板 → 意外指数。
- 本期所有新增数据遵循现有 CSV 双轨模式（宏观系列观测日 upsert / 日频快照按日追加），分析层与 Web 层无需改动即可消费。
