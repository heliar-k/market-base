# 数据目录 (DATA CATALOG)

所有数据以 CSV 存储，`date` 列统一为首列（ISO 格式），`pd.read_csv(path, index_col='date', parse_dates=True)` 直接可用。

宏观时间序列（FRED / Shapiro / SCE / CBOE / OFR / NY Fed Markets）以**观测日**为 key，每次 `./bin/fetch_*` 拉源全量历史并 upsert（同日新值覆盖旧值，新日追加）→ 忘记运行自动补漏。股票/指数/期货以交易日为 key 增量追加。

---

## 1. 宏观指标 — `data/fred/{category}/` （12 分类）

12 个分类、65 个系列，来源 FRED API。每次 `./bin/fetch_fred` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。

| 分类 | 路径 | 系列 | 内容 |
|------|------|------|------|
| `volatility` | `data/fred/volatility/volatility.csv` | 3 | VIX / HY_OAS / IG_OAS |
| `inflation` | `data/fred/inflation/inflation.csv` | 19 | CPI / PCE / 核心 / CPI细分 / Super-core / BEI / 通胀预期 |
| `labor` | `data/fred/labor/labor.csv` | 3 | 失业率 / 非农 / 首申失业金 |
| `growth` | `data/fred/growth/growth.csv` | 5 | 实际GDP / 工业产出 / 实际PCE / 产能利用率 / 制造业新订单 |
| `rates` | `data/fred/rates/rates.csv` | 14 | 联邦基金利率 / SOFR/TGCR/BGCR/ONRRP / 国债全期限 |
| `tips` | `data/fred/tips/tips.csv` | 5 | 5Y-30Y TIPS 实际收益率 |
| `liquidity` | `data/fred/liquidity/liquidity.csv` | 5 | NFCI / 准备金 / RRP / TGA / 联储总资产 |
| `sentiment` | `data/fred/sentiment/sentiment.csv` | 2 | 消费者信心 / 金融压力指数 |
| `fx` | `data/fred/fx/fx.csv` | 1 | 贸易加权美元指数 |
| `producer_prices` | `data/fred/producer_prices/producer_prices.csv` | 4 | PPI Final Demand / 核心PPI / 分项 |
| `consumption` | `data/fred/consumption/consumption.csv` | 1 | 个人储蓄率 |
| `labor_market` | `data/fred/labor_market/labor_market.csv` | 4 | JOLTS 职位空缺/离职率 + 失业人数 + ECI 工资 |

### 列名速查
`volatility`: VIX, HY_OAS, IG_OAS

> ⚠️ 发布滞后：ICE BofA 信用利差系列（HY_OAS/IG_OAS）比 VIXCLS 滞后 1~2 个交易日，
> 最新一行可能 VIX 有值而 OAS 为空——不是拉取 bug，下次 fetch 自动补上（upsert）。
> 此分类的 VIX 与 `data/cboe/volatility.csv` 的 VIX 同源（都是 CBOE 官方收盘），可互验。
`inflation`: CPI, PCE, CORE_CPI, CORE_PCE, CPI_SHELTER, CPI_FOOD, CPI_ENERGY, CORE_SERVICES, CORE_GOODS, SUPERCORE_PCE, SUPERCORE_PCE_REAL, T5YIE, T10YIE, T5YIFR, MICH, EXPINF_1Y, EXPINF_2Y, EXPINF_5Y, EXPINF_10Y
`labor`: UNRATE, PAYEMS, ICSA
`growth`: GDP, INDPRO, REAL_PCE, CAPU, DGORDER
`rates`: FEDFUNDS, DFEDTARL, DFEDTARU, SOFR, SOFR1/25/75/99, SOFRVOL, OBFR, IORB, TGCR, ONRRP, BGCR*, DGS1MO...DGS30

> `BGCR`（Broad General Collateral Rate）不在 FRED，由 `./bin/fetch_bgcr` 从 NY Fed
> Markets API 拉取并合并进 rates.csv（TGCR ⊂ BGCR ⊂ SOFR；TGCR/BGCR 自 2021-03-01 发布）。

> `CGB`（中国国债 10Y/30Y 收益率）不在 FRED，由 `./bin/fetch_cgb` 从 chinamoney
> 实时曲线拉取，存 `data/fred/rates/cgb.csv`（独立文件不并入 rates.csv；
> yield-curve 页全球长端对照 + overview 研判用）。
`tips`: DFII5, DFII7, DFII10, DFII20, DFII30
`liquidity`: NFCI, RRPONTSYD, WTREGEN, WRESBAL, WALCL
`sentiment`: UMCSENT, STLFSI4
`fx`: DXY
`producer_prices`: PPI_FD, CORE_PPI, PPI_GOODS, PPI_SERVICES
`consumption`: PSAVERT
`labor_market`: JOLTS_OPEN, JOLTS_QUITS, UNEMPLOY, ECI_WAGES

---

## 2. 波动率 — `data/cboe/volatility.csv`

来源 CBOE CDN。每次 `./bin/fetch_cboe` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。
VIX9D/VIX/VIX3M/VIX6M/VIX1Y 全量序列一并落盘，可复算期限结构斜率。

| 列名 | 说明 |
|------|------|
| `OVX` | CBOE 原油波动率指数 |
| `VIX9D` | 9 天 VIX |
| `VIX` | 30 天 VIX（CBOE 原始序列；FRED VIXCLS 也有） |
| `VIX3M` | 3 个月 VIX（2009-09 起） |
| `VIX6M` | 6 个月 VIX（2008-01 起） |
| `VIX1Y` | 1 年 VIX（2007-01 起） |
| `VIX_TERM_SLOPE` | VIX 期限结构斜率（VIX - VIX9D），正=contango，负=backwardation |

> 与 `data/fred/volatility/volatility.csv` 的关系：仅 VIX 一列重叠（同源），fred 版
> 侧重信用利差（risk-off），本文件侧重波动率期限结构，用途不同不统一。
> 本文件已纳入 Actions daily-fetch 自动拉取（`bin/fetch_cboe`）；若某日 auto-fetch
> commit 里没有它的更新，说明 CBOE CDN 无新数据（或上次手动已拉到最新），不是漏跑。

---

## 2b. 金融压力 — `data/ofr/fsi.csv`

来源 OFR（美国财政部金融研究办公室）官方 CSV，日频、2000 年至今全历史，发布滞后 2 个工作日。
每次 `./bin/fetch_fsi` 拉全量历史并 upsert（漏跑自动补）。

| 列名 | 说明 |
|------|------|
| `OFR_FSI` | OFR 金融压力指数（正=高于常态压力） |
| `CREDIT` / `EQUITY_VALUATION` / `SAFE_ASSETS` / `FUNDING` / `VOLATILITY` | 五个成分分项 |
| `US` / `OTHER_ADVANCED` / `EMERGING_MARKETS` | 分区域贡献 |

---

---

## 3. 流动性原始系列 — `data/fred/liquidity/liquidity.csv`

FRED liquidity 分类的原始系列（由 `./bin/fetch_fred` 一并拉取）。派生指标
`NET_LIQUIDITY = WALCL − RRPONTSYD×1000 − WTREGEN` 由 `src.macro.derive_macro()`
现算，不落盘。

| 列名 | 说明 | 单位 |
|------|------|------|
| `NFCI` | 金融状况指数 | 指数 |
| `RRPONTSYD` | 隔夜逆回购 | 十亿美元 |
| `WTREGEN` | 财政部一般账户 (TGA) | 百万美元 |
| `WRESBAL` | 准备金余额 | 百万美元 |
| `WALCL` | 联储总资产 | 百万美元 |

> 派生 `NET_LIQUIDITY` 见 `src/macro.py`；RRP 单位十亿美元，计算时 ×1000 统一到百万。

---

## 3a. SRF 使用量 — `data/fred/liquidity/srf.csv`

来源 NY Fed Markets API（官方、免费、无 key），由 `./bin/fetch_srf` 拉取：首次全历史，之后每日增量 5 天 upsert。

| 列名 | 说明 | 单位 |
|------|------|------|
| `SRF_USAGE` | Standing Repo Facility 日度使用量 | 十亿美元 |

> **识别规则**（2026-08 验证）：
> - 2021-07-29（上线）~ 2025-12-10：SRF 以 Multiple Price 拍卖运行，每日 13:30 一场，
>   此段精确（如 2025-10-31 = 50.35B，与 Reuters 一致）。排除 10:30 场（SVE 测试）。
> - 2025-12-11 起：SRF 改为 Full Allotment，每日固定两场（08:15 早场 + 13:30 下午场），
>   operationType=Repo + Full Allotment 即 SRF（POMO/RMP 国债购买在另一套 API，不混淆）。
>   与 FRED `RPONTTLD`（全部 repo 总和）逐日交叉验证零超限。
> - SVE（Small Value Exercise 测试操作：切换前 10:30 场 / 切换后编号 99）不计入。

---

## 3b. Treasury 公开市场操作明细 — `data/fred/liquidity/tsy_operations.csv`

来源 NY Fed Markets API `/api/tsy/{purchases,sales}/results/details/last/300.json`
（官方、免费、无 key），由 `./bin/fetch_tsy` 拉取：每次全量覆盖写（最近 300 笔，约 5 年）。

| 列名 | 说明 | 单位 |
|------|------|------|
| `operation_id` | 操作 ID | — |
| `date` | 操作日 | — |
| `settlement_date` | 结算日 | — |
| `operation_type` | Outright Bill/Coupon/TIPS/FRN Purchase/Sale | — |
| `is_rmp` | 2025-12-12（RMP 启动）后的 Bill Purchase = Reserve Management Purchases | bool |
| `maturity_start/end` | 到期范围 | — |
| `submitted_b/accepted_b` | 提交额 / 接受额 | 十亿美元 |
| `accept_ratio` | 接受比（接受/提交） | % |
| `note` | 备注 | — |

> RMP 为技术性操作（补充银行准备金，每月约 $40B 国库券购买），非 QE；识别口径与
> timsun.net 一致（截至 2026-06-09 为 40 笔 / $283.84B，交叉验证吻合）。

---

## 3c. CFETS 外汇掉期点 — `data/fred/liquidity/cfets_swap_points.csv`

来源中国外汇交易中心 CFETS 外汇掉期曲线（官方、免费、无 key），由 `./bin/fetch_cfets` 拉取：
每日观测日 upsert（交易日 16:30 发布，17:00 可查；当天重跑同日期覆盖）。

| 列名 | 说明 | 单位 |
|------|------|------|
| `{PAIR}_{TENOR}`（EURUSD/USDJPY/GBPUSD/AUDUSD/USDHKD × 1W/1M/3M/6M/1Y） | 外汇掉期点（CFETS） | pips（1 pip = 0.0001） |
| `USDCNH_/USDCHF_{TENOR}`（ON/TN/SN/1W/2W/3W/1M~11M/1Y/2Y/3Y，4Y+ 多为 N/A） | 外汇掉期点（Barchart 远期点曲线 bid/ask 中值） | 同上 |
| `USDCNH_NEAR` / `USDCHF_NEAR` | 近月掉期点（优先 Barchart 1M，Barchart 失败时降级 Yahoo CME 主连） | 同上 |

> 掉期点 = 远期汇率 − 即期汇率（以 pip 计），负值表示外币相对美元贴水。
> 覆盖 5 个外币对（timsun global-dollar 页面的 USD/JPY、EUR/USD、GBP/USD 在内）。
> USD/CNH、USD/CHF 不在 CFETS 覆盖范围（页面亦标注“暂未覆盖”），改用 Barchart
> forward-rates 页面的全期限远期点曲线（匿名两步请求：页面种 cookie → core-api 带
> XSRF token，`quotes/get?lists=forex.forwardCurves(^PAIR)`），延迟报价；数值与
> investing.com 远期点页面对拍一致（差异 <1%）。
> Yahoo CME 期货主连（`CNH=F` / `6S=F`）仅作 Barchart 失败时的降级，近月单点。
> 注意：本 fetcher 对 chinamoney 绕过本地 SOCKS5 代理直连（TLS legacy renegotiation
> 走代理握手失败），Barchart/Yahoo 部分走代理（本地）/直连（Actions），互不影响。

---

## 4. 股票日线 — `data/stocks/{SYMBOL}.csv`

20 只股票，来源 IBKR。每日 `./bin/fetch_ibkr` 更新。

| 文件 | 品种 |
|------|------|
| `AAPL.csv` | Apple |
| `AMZN.csv` | Amazon |
| `GOOG.csv` | Google (Alphabet) |
| `META.csv` | Meta |
| `MSFT.csv` | Microsoft |
| `MU.csv` | Micron |
| `NVDA.csv` | NVIDIA |
| `QQQ.csv` | Invesco QQQ ETF |
| `SNDK.csv` | SanDisk (闪迪) |
| `SPY.csv` | SPDR S&P 500 ETF |
| `TSLA.csv` | Tesla |
| `TSM.csv` | TSMC (台积电) |

### 列格式（OHLCV）

| 列名 | 说明 |
|------|------|
| `date` | 日期（YYYY-MM-DD） |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价 |
| `volume` | 成交量 |
| `wap` | 均价（VWAP，旧称 `average`） |
| `count` | 日内棒数（旧称 `barCount`） |

---

## 5. 指数日线 — `data/indices/{SYMBOL}.csv`

4 个指数，来源 IBKR。格式同上 OHLCV。

| 文件 | 品种 |
|------|------|
| `SPX.csv` | S&P 500 指数 |
| `IXIC.csv` | 纳斯达克综合指数 |
| `RUT.csv` | Russell 2000 |
| `VIX.csv` | CBOE VIX 指数 |

---

## 6. 期权链 — `data/options/`

10 只品种的期权链参数。

| 文件 | 内容 |
|------|------|
| `{SYMBOL}_chain.json` | 完整参数（到期日、行权价、交易所） |
| `{SYMBOL}_grid.csv` | 到期日 × 行权价格网 |

---

## 7. 期货 — `data/commodities/{SYMBOL}/{SYMBOL}_{YYYYMM}.csv`

10 个期货品种，来源 IBKR，自动拉取全部未过期合约。每日 `./bin/fetch_commodities` 更新。

> **Barchart 降级/补充源** — `data/barchart/futures/{ROOT}.csv`（观测日 upsert 宽表，
> 列 = 合约代码如 ESU31）：由 `./bin/fetch_barchart_futures` 拉取（免费匿名，Actions 每日跑），
> 覆盖全部 10 品种的整条合约曲线（含 RTY→TF 代码映射），延迟报价（lastPrice 带 s 后缀）。
> ZQ 额外写 `data/barchart/commodities/ZQ/ZQ_{YYYYMM}.csv`（date/close），
> `rate_expectations` 读取优先级：IBKR 本地 → Barchart，因此 FOMC 概率在 Actions 也可每日自动产出。

### 商品期货

| Symbol | 品种 | 交易所 |
|--------|------|--------|
| `GC` | Gold | COMEX |
| `CL` | WTI Crude | NYMEX |
| `NG` | Natural Gas | NYMEX |
| `SI` | Silver | COMEX |
| `HG` | Copper | COMEX |

### 股指期货

| Symbol | 品种 | 交易所 |
|--------|------|--------|
| `ES` | S&P 500 E-mini | CME |
| `NQ` | Nasdaq-100 E-mini | CME |
| `YM` | Dow E-mini | CBOT |
| `RTY` | Russell 2000 E-mini | CME |

### 利率期货

| Symbol | 品种 | 交易所 |
|--------|------|--------|
| `ZQ` | 30-Day Fed Funds | CBOT |

每个合约月一个 CSV，OHLCV 格式同股票/指数。

### Grid CSV 列格式

| 列名 | 说明 |
|------|------|
| `expiry` | 到期日（YYYY-MM-DD） |
| `strike` | 行权价（float） |
| `exchange` | 交易所（如 SMART、CBOE） |
| `trading_class` | 期权类别（如 AAPL、TSLA） |

---

## 8. Shapiro 供给/需求 PCE 通胀分解 — `data/shapiro/shapiro.csv`

来源 FRBSF（旧金山联储 Shapiro 分解）。每月 PCE 发布后几天更新，`./bin/fetch_shapiro` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。
4 个 chart CSV（headline/core × monthly年化/yoy），各拆出 supply/demand/ambiguous 贡献（pp）。

| 列名 | 说明 |
|------|------|
| `SHAPIRO_{HEADLINE|CORE}_{MOM|YOY}_SUPPLY` | 供给驱动贡献（pp） |
| `SHAPIRO_{HEADLINE|CORE}_{MOM|YOY}_DEMAND` | 需求驱动贡献（pp） |
| `SHAPIRO_{HEADLINE|CORE}_{MOM|YOY}_AMBIG` | 模糊类贡献（pp） |

> MOM = 年化月度贡献（最及时但噪声大）；YOY = 同比贡献（趋势）。
> 三项之和 ≡ 对应 PCE 通胀率。Demand 高 = 需求拉动，Supply 高 = 供给冲击。

---

## 9. NY Fed SCE 通胀预期 — `data/sce/sce.csv`

来源 NY Fed Survey of Consumer Expectations。每月第二个周一发布，`./bin/fetch_sce` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。
全量 Excel 45 个 sheet，此处取 1Y/3Y 通胀预期中位数。

| 列名 | 说明 |
|------|------|
| `SCE_INFL_1Y_MEDIAN` | 1 年期通胀预期中位数（%） |
| `SCE_INFL_3Y_MEDIAN` | 3 年期通胀预期中位数（%） |

> Excel 另含劳动力/房价/信贷/消费等 40+ sheet，按需在 `sce_fetcher.py` 扩展。

---

## 10. 国债拍卖 — `data/treasury/`

来源 US Treasury Fiscal Data API（免费，无需认证）。`./bin/fetch_treasury` 拉取拍卖结果和未来日历，每次全量覆盖。

### auction_results.csv（拍卖结果，~11,000 条历史，全量覆盖）

| 列名 | 说明 |
|------|------|
| `security_type` | 券种：Bill / Note / Bond / TIPS / FRN |
| `security_term` | 期限：4-Week / 13-Week / 2-Year / 10-Year / 30-Year 等 |
| `offering_amt` | 发行额（USD） |
| `bid_to_cover_ratio` | 投标倍数（>2.5x 需求良好，<2.0x 警戒） |
| `high_yield` | 中标收益率（%，Note/Bond/TIPS） |
| `high_rate` | 统一中标利率（%）：Bill 取 high_discnt_rate（贴现率），其余取 high_yield |
| `avg_med_yield` | 中位投标利率（%），用于计算 Tail |
| `indirect_pct` | 间接投标人占比（%）= 外国官方 + 国际机构，反映海外需求 |
| `tail_bp` | 拍卖 Tail（bp）= (high_yield − avg_med_yield) × 100，正=弱于预期 |
| `indirect_bidder_accepted` | 间接投标人接受额（原始值） |
| `total_accepted` | 总接受额（原始值） |
| `reopening` | 是否重开 |
| `cusip` | CUSIP 代码 |
| `issue_date` | 发行日 |
| `maturity_date` | 到期日 |

### upcoming_auctions.csv（未来拍卖日历，~93 条，全量覆盖）

## 10b. 多周期 K 线 — 同目录后缀式 `{NAME}_{周期}.csv`

与日线同目录（`data/stocks/` / `data/indices/`），周期为文件名后缀：

| 文件 | 来源 | 说明 |
|---|---|---|
| `AAPL_5m.csv` / `_15m` / `_1h` / `_4h` | IBKR 分钟线（需 TWS） | 含 UTC 时间戳，深度受 IBKR 限制（实测 5m 可拉 ~1.5 年） |
| `AAPL_1w.csv` | 本地日线重采样（W-FRI，无需 TWS） | `open/high/low/close/volume`，周五截止 |
| `AAPL.csv` | 日线（无后缀，保持兼容） | TUI/analyze 读取路径不变 |

```bash
./bin/fetch_ibkr --bar-size 5m --symbols AAPL   # 分钟线（增量，只补新 bar）
./bin/fetch_ibkr --bar-size 1w                  # 周线（全部品种，无需 TWS）
```

## 11. yfinance 资产价格快照 — `data/yfinance/asset_prices.csv`

拉取日为 key 的日频快照（每日一行，同日期覆盖），GitHub Actions 每日自动拉取。
需要 SOCKS5 代理（本地）；Actions 无代理环境用 `YF_NO_PROXY=1` 直连。

| 类别 | 品种 |
|---|---|
| 指数 | SPX、NDX、RUT、DJI、SOX、N225（日经）、KOSPI、NIFTY、SSE（上证）、SZSE（深证） |
| 汇率 | DXY、USDJPY（美元兑日元）、USDCNY（美元兑人民币） |
| 波动率 | MOVE（美林国债期权波动率，债市 VIX） |
| 加密 | BTC |
| 商品 | WTI、Brent、Gold、Silver、Copper |
| 债券 ETF | TLT、HYG、LQD |
| 韩股 | SAMSUNG（三星电子）、SKHYNIX（SK 海力士）——纯数字 KRX 代码用可读名，yf_ticker 保留 005930.KS/000660.KS |

| 列名 | 说明 |
|------|------|
| `security_type` | 券种 |
| `security_term` | 期限 |
| `offering_amt` | 计划发行额（USD） |
| `reopening` | 是否重开 |
| `cusip` | CUSIP 代码 |
| `issue_date` | 发行日 |

---

## 12. COT 持仓报告 — `data/cot/cot.csv`

CFTC 官方周度持仓报告（周二数据、周五发布），`./bin/fetch_cot` 拉取（免费，Actions 每日跑，
默认当年+去年全量 upsert）。观测日 = 报告日期。

| 列名 | 说明 |
|------|------|
| `{SYM}_OI` | 总持仓（Open Interest） |
| 商品类（disaggregated）`{SYM}_PROD_L/S`、`{SYM}_SWAP_L/S`、`{SYM}_MM_L/S` | 生产商/商户、掉期商、管理资金（投机）多/空持仓 |
| 金融类（TFF）`{SYM}_DEALER_L/S`、`{SYM}_ASSET_L/S`、`{SYM}_HEDGE_L/S` | 做市商、资管、对冲基金多/空持仓 |

覆盖品种：GC/SI/HG/CL/NG（disaggregated）+ ES/NQ/RTY/ZQ（TFF）。
YM（道指）不在 CFTC COT 报告中（2024-26 均无）。

## 速查：怎么用

```python
import pandas as pd

# 按分类读 FRED 数据
rates = pd.read_csv('data/fred/rates/rates.csv', index_col='date', parse_dates=True)
infl = pd.read_csv('data/fred/inflation/inflation.csv', index_col='date', parse_dates=True)
labor = pd.read_csv('data/fred/labor/labor.csv', index_col='date', parse_dates=True)

# 或合并所需的分类
macro = pd.concat([
    pd.read_csv(f'data/fred/{c}/{c}.csv', index_col='date', parse_dates=True)
    for c in ['rates', 'tips', 'inflation', 'liquidity']
], axis=1)

# 算 5Y BEI
macro['BEI_5Y'] = (macro['DGS5'] - macro['DFII5']) * 100
# 算 2s10s 利差
macro['2s10s'] = macro['DGS10'] - macro['DGS2']

# 流动性派生指标（由 derive_macro 现算，不落盘）
liq_raw = pd.read_csv('data/fred/liquidity/liquidity.csv', index_col='date', parse_dates=True)
from src.macro import derive_macro
liq = derive_macro(liq_raw)[['NET_LIQUIDITY']]

# 波动率
vol = pd.read_csv('data/cboe/volatility.csv', index_col='date', parse_dates=True)

# Shapiro 供给/需求分解
shapiro = pd.read_csv('data/shapiro/shapiro.csv', index_col='date', parse_dates=True)

# NY Fed SCE 通胀预期
sce = pd.read_csv('data/sce/sce.csv', index_col='date', parse_dates=True)

# 股票 K 线
aapl = pd.read_csv('data/stocks/AAPL.csv', index_col='date', parse_dates=True)

# 指数日线
spx = pd.read_csv('data/indices/SPX.csv', index_col='date', parse_dates=True)

# 国债拍卖
auction_results = pd.read_csv('data/treasury/auction_results.csv', index_col='auction_date', parse_dates=True)
upcoming = pd.read_csv('data/treasury/upcoming_auctions.csv', index_col='auction_date', parse_dates=True)
```

---

## 13. 利率专题页（Web，复刻 timsun.net/rates）

`uv run python -m src.server` 后访问 `http://localhost:8000/rates/`，共 6 页：

| 页面 | 路由 | 数据源 |
|------|------|--------|
| 利率研判（入口） | `/rates/` | `src/rates_analysis.py` 规则引擎四段文 |
| 联邦基金利率 | `/rates/fed-funds.html` | rates.csv（EFFR/SOFR/走廊/成交量） |
| 收益率曲线 | `/rates/yield-curve.html` | rates.csv + tips.csv + 规则引擎解读 |
| 国债拍卖 | `/rates/auctions.html` | `data/treasury/`（auction_results + upcoming） |
| 实际利率 | `/rates/real-rates.html` | rates.csv + tips.csv |
| 利率预期 | `/rates/expectations/` | `data/rate_expectations/fomc_probabilities.csv` |

### API 端点

- `GET /api/rates/analysis` — 利率研判（规则引擎，含 overview 四段文 + yield_curve 解读）
- `GET /api/rates/fed-funds` — EFFR/SOFR/目标区间/走廊/成交量/EFFR 5 年历史
- `GET /api/rates/yield-curve` — 四线快照 + 利差时序 + 规则解读
- `GET /api/rates/real-rates` — 10Y 名义/TIPS/盈亏平衡 + 2Y + 2s10s（近 1 年）
- `GET /api/rates/auctions` — 需求概览 + 近 90 天结果 + 未来 21 天日历 + 2/5/10/30Y 趋势

### 规则引擎（`src/rates_analysis.py`）

确定性推导曲线形态（熊陡/牛陡/熊平/牛平）、驱动归因（实际利率 vs 盈亏平衡）、
验证指标确认度、交易含义、失效条件。**LLM 预留**：`generate_analysis()` 内
`_llm_generate()` 为替换点，实现后置 `_LLM_ENABLED = True` 即切换，
API 响应 `generator` 字段标记 `rules` / `llm`，前端无感。

---

## 14. 美联储鹰鸽面板（Web，复刻 timsun.net/fed）

`./bin/fetch_fed` 拉取 federalreserve.gov（直连，不受 SOCKS5 代理影响），
`uv run python -m src.server` 后访问 `http://localhost:8000/fed/`，共 4 页：

| 页面 | 路由 | 数据源 |
|------|------|--------|
| 美联储（入口） | `/fed/` | 鹰鸽指示器 + 下次 FOMC 倒计时 + 最新声明/演讲 |
| FOMC 声明 | `/fed/statements.html` | `data/fed/statements.csv`（声明/纪要/SEP/贴现率纪要） |
| 官员演讲 | `/fed/speeches.html` | `data/fed/speeches.csv`（近 2 年） |
| 鹰鸽追踪 | `/fed/hawkish-dovish.html` | 立场时间线 + 官员最新立场表 |

### 数据文件

- `data/fed/statements.csv` — `id, date, kind, title, url, body`，kind ∈
  statement / minutes / sep / discount / other，声明自 2020 年起
- `data/fed/speeches.csv` — `id, date, speaker, title, url, body`，近 2 个自然年
- 增量：按 URL 去重，本地已有则跳过（列表页每年 1 请求，正文页首次全量后不再抓）

### API 端点

- `GET /api/fed/overview` — 鹰鸽指示器 + 声明/演讲列表（含评分）+ 官员立场 + 时间线
- `GET /api/fomc/calendar` — 下次 FOMC 会议日期 + 目标利率区间（已有）

### 鹰鸽评分（`src/fed_analysis.py`）

关键词短语词表对英文正文打分（-5 极鸽 ~ +5 极鹰）：动作词（加息/降息决议）
权重最高且声明 > 演讲（演讲中"提及降息"≠"倾向降息"），通胀/就业/语气词次之，
反对票方向计入内部压力。**LLM 预留**：`generate_fed_analysis()` 内
`_llm_generate()` 为替换点，实现后置 `_LLM_ENABLED = True` 即切换，
API 响应 `generator` 字段标记 `rules` / `llm`，前端无感。
