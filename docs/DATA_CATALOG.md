# 数据目录 (DATA CATALOG)

所有数据以 CSV 存储，`date` 列统一为首列（ISO 格式），`pd.read_csv(path, index_col='date', parse_dates=True)` 直接可用。

宏观时间序列（FRED / Shapiro / SCE / CBOE / OFR / NY Fed Markets）以**观测日**为 key，每次 `./bin/fetch_*` 拉源全量历史并 upsert（同日新值覆盖旧值，新日追加）→ 忘记运行自动补漏。股票/指数/期货以交易日为 key 增量追加。

---

## 1. 宏观指标 — `data/fred/{category}/` （13 分类）

13 个分类、91 个系列，来源 FRED API。每次 `./bin/fetch_fred` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。

| 分类 | 路径 | 系列 | 内容 |
|------|------|------|------|
| `volatility` | `data/fred/volatility/volatility.csv` | 3 | VIX / HY_OAS / IG_OAS |
| `inflation` | `data/fred/inflation/inflation.csv` | 19 | CPI / PCE / 核心 / CPI细分 / Super-core / BEI / 通胀预期 |
| `labor` | `data/fred/labor/labor.csv` | 3 | 失业率 / 非农 / 首申失业金 |
| `growth` | `data/fred/growth/growth.csv` | 5 | 实际GDP / 工业产出 / 实际PCE / 产能利用率 / 制造业新订单 |
| `rates` | `data/fred/rates/rates.csv` | 16 | 联邦基金利率（DFF 日频 + FEDFUNDS 月频） / SOFR/TGCR/BGCR/ONRRP / 国债全期限 + ACMTP10 |
| `tips` | `data/fred/tips/tips.csv` | 5 | 5Y-30Y TIPS 实际收益率 |
| `liquidity` | `data/fred/liquidity/liquidity.csv` | 5 | NFCI / 准备金 / RRP / TGA / 联储总资产 |
| `sentiment` | `data/fred/sentiment/sentiment.csv` | 2 | 消费者信心 / 金融压力指数 |
| `fx` | `data/fred/fx/fx.csv` | 1 | 贸易加权美元指数 |
| `producer_prices` | `data/fred/producer_prices/producer_prices.csv` | 4 | PPI Final Demand / 核心PPI / 分项 |
| `consumption` | `data/fred/consumption/consumption.csv` | 1 | 个人储蓄率 |
| `labor_market` | `data/fred/labor_market/labor_market.csv` | 4 | JOLTS 职位空缺/离职率 + 失业人数 + ECI 工资 |
| `tic` | `data/fred/tic/tic.csv` | 7 | TIC 美债净买入（总额/官方）+ 官方持仓 + 日本/中国/沙特/阿联酋持仓 |

### 列名速查
`volatility`: VIX, HY_OAS, IG_OAS

> ⚠️ 发布滞后：ICE BofA 信用利差系列（HY_OAS/IG_OAS）比 VIXCLS 滞后 1~2 个交易日，
> 最新一行可能 VIX 有值而 OAS 为空——不是拉取 bug，下次 fetch 自动补上（upsert）。
> 此分类的 VIX 与 `data/cboe/volatility.csv` 的 VIX 同源（都是 CBOE 官方收盘），可互验。
`inflation`: CPI, PCE, CORE_CPI, CORE_PCE, CPI_SHELTER, CPI_FOOD, CPI_ENERGY, CORE_SERVICES, CORE_GOODS, SUPERCORE_PCE, SUPERCORE_PCE_REAL, T5YIE, T10YIE, T5YIFR, MICH, EXPINF_1Y, EXPINF_2Y, EXPINF_5Y, EXPINF_10Y
`labor`: UNRATE, PAYEMS, ICSA
`growth`: GDP, INDPRO, REAL_PCE, CAPU, DGORDER
`rates`: DFF, FEDFUNDS, DFEDTARL, DFEDTARU, SOFR, SOFR1/25/75/99, SOFRVOL, OBFR, IORB, TGCR, ONRRP, BGCR*, DGS1MO...DGS30, ACMTP10*

> `ACMTP10`（NY Fed ACM 模型 10Y 期限溢价）不在 FRED，由 `./bin/fetch_acm` 从
> NY Fed 底稿 ACMTermPremium.xls 的 **ACM Daily** sheet 拉取（官方日频估算，
> 1961-06 起）并合并进 rates.csv；同文件 ACM Monthly sheet 为月末重估。
> 阈值参考：>+0.85% 偏空 / <+0.50% 偏多 / 单月跳>15bp 告警。

> `DFF` = 有效联邦基金利率（日频，1999-03 起），fed-funds 页 EFFR 图/走廊用；
> `FEDFUNDS` 是月频均值（1954 起，仅作历史兜底），不能按日频绘制。

> `BGCR`（Broad General Collateral Rate）不在 FRED，由 `./bin/fetch_bgcr` 从 NY Fed
> Markets API 拉取并合并进 rates.csv（TGCR ⊂ BGCR ⊂ SOFR；TGCR/BGCR 自 2021-03-01 发布）。

> `CGB`（中国国债 10Y/30Y 收益率）不在 FRED，由 `./bin/fetch_cgb` 从 chinamoney
> 实时曲线拉取，存 `data/fred/rates/cgb.csv`（独立文件不并入 rates.csv；
> yield-curve 页全球长端对照 + overview 研判用）。
`tips`: DFII5, DFII7, DFII10, DFII20, DFII30
`liquidity`: NFCI, ANFCI, NFCIRISK, NFCICREDIT, NFCILEVERAGE, RRPONTSYD, WTREGEN, WRESBAL, WRBWFRBL, WALCL, TREAST, WSHOMCB, SWPT

> `credit`（信用市场）：AAA/BAA 为 Moody's 月度收益率；BBB_OAS/BB_OAS/B_OAS/CCC_OAS 为
> ICE BofA 分层 OAS（日频）；IG_YIELD/HY_YIELD 为 ICE BofA 有效收益率（日频）；
> SLOOS_* 为季度银行信贷标准/需求；DELINQ_*/CHGOFF_* 为季度贷款质量。
> ⚠️ FRED API 对 ICE BofA 系列仅返回近 ~3 年（795 条），10Y 分位按可用历史计算（与 timsun 同限制）。
`sentiment`: UMCSENT, STLFSI4
`fx`: DXY
`producer_prices`: PPI_FD, CORE_PPI, PPI_GOODS, PPI_SERVICES
`consumption`: PSAVERT
`tic`: TIC_NET_TOTAL, TIC_NET_OFFICIAL, TIC_HOLD_OFFICIAL, TIC_HOLD_TOTAL, TIC_HOLD_JAPAN, TIC_HOLD_CHINA, TIC_HOLD_SAUDI, TIC_HOLD_UAE

> `tic`（Treasury International Capital，月度，滞后 2 月；FRED 转发 TIC 官方数据）：
> 净买入/持仓均为百万美元。监控阈值：官方单月净抛>400 亿告警、连续 3 月净抛强告警；
> 中国持仓<7000 亿告警。持仓用 FORTREASPOS*（LT+ST 总额，Table 5 口径），
> 勿用 FORLTTREASPOS*（LT-only——中国持仓将永远低于阈值）。
> 海外官方占比（<23% 结构性偏空）= TIC_HOLD_OFFICIAL ÷ data/treasury/mspd.csv 的
> TOTAL_DEBT（总未偿债务；GFDEBTN 在 FRED 侧已停更，勿用）。
`labor_market`: JOLTS_OPEN, JOLTS_QUITS, UNEMPLOY, ECI_WAGES

---

## 2. 波动率 — `data/cboe/volatility.csv`

来源 CBOE CDN。每次 `./bin/fetch_cboe` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。
VIX1D/VIX9D/VIX/VIX3M/VIX6M/VIX1Y/SKEW 全量序列一并落盘，可复算期限结构斜率。
2026-08 扩展至 24 列（timsun 波动率面板对齐）：新增商品/股指/期限/尾部/个股波动率指数。
列名用 timsun 面板显示名，部分与 CBOE 官方代码不同（VXSL→VXSLV、VTLT→VXTLT、VXGO→VXGOG、VXAP→VXAPL、VXAZ→VXAZN、VXIB→VXIBM）。

| 列名 | 说明 |
|------|------|
| `OVX` | CBOE 原油波动率指数 |
| `VIX1D` | 1 天 VIX（2022-05 起，期限结构最前端） |
| `VIX9D` | 9 天 VIX |
| `VIX` | 30 天 VIX（CBOE 原始序列；FRED VIXCLS 也有） |
| `VIX3M` | 3 个月 VIX（2009-09 起） |
| `VIX6M` | 6 个月 VIX（2008-01 起） |
| `VIX1Y` | 1 年 VIX（2007-01 起） |
| `SKEW` | 看跌偏斜指数（1990 起），>140 尾部对冲需求偏高 |
| `VIX_TERM_SLOPE` | VIX 期限结构斜率（VIX - VIX9D），正=contango，负=backwardation |
| `GVZ` | 黄金波动率（2008-03 起） |
| `VXSL` | 白银波动率（官方 VXSLV，2011-03 起） |
| `VXN` | 纳指 100 波动率（2001-01 起） |
| `VXD` | 道琼斯波动率（1997-07 起） |
| `VVIX` | 波动率的波动率（2007-01 起） |
| `VXV` | 3 个月 VIX（与 VIX3M 同义；CDN 上 VXV 历史仅 23 行（2017-09~10，源数据异常），实质用 VIX3M 列） |
| `VIN` / `VIF` | Near / Far Term VIX（2008-08 起，4000+ 行） |
| `VXTH` | Tail Hedge 指数（2007-01 起） |
| `VTLT` | 20Y 国债波动率（官方 VXTLT，2004-01 起） |
| `VXGO` / `VXGS` / `VXAP` / `VXAZ` / `VXIB` | 个股波动率（Google/高盛/Apple/Amazon/IBM，2011-01 起） |
| `VXNG` | 天然气波动率（官方 VXUNG，2020-11 起） |
| `VXEEM` | 新兴市场 ETF 波动率（官方 VXEEM，2011-03 起） |

### 指数别名映射（调研确认，2026-08-06）

| timsun 面板名 | 本库实际列 | 说明 |
|---|---|---|
| VXMT（中期 VIX） | `VIX6M` | 同一指数（官方定义 6 个月 SPX 波动率），数值已验证一致 |
| VXV（3 个月 VIX） | `VIX3M` | 同一指数（2018 更名），重叠期数值 diff=0 |
| VXNG（天然气） | `VXNG`（官方 VXUNG） | timsun 显示名 vs 官方代码，数值已验证一致 |
| VEEM（新兴市场） | `VXEEM` | timsun 显示名 vs 官方代码，数值已验证一致 |

> 无免费源（CDN 403，Yahoo/FRED/stooq/Barchart 均验证无）：VXHY（高收益债 VIX）、
> VEWZ（巴西 ETF VIX）、VXMO（Standard Monthly VIX，2024 新推）。
> VXEF（MSCI 新兴市场 VIX）：CBOE 2026 公告停止 MSCI 衍生品指数系列。

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
| `ABBV.csv` | AbbVie (艾伯维) |
| `AZN.csv` | AstraZeneca ADR (阿斯利康) |
| `AMZN.csv` | Amazon |
| `COST.csv` | Costco (好市多) |
| `GOOG.csv` | Google (Alphabet) |
| `HD.csv` | Home Depot (家得宝) |
| `ISRG.csv` | Intuitive Surgical (直觉外科) |
| `JNJ.csv` | Johnson & Johnson (强生) |
| `META.csv` | Meta |
| `MSFT.csv` | Microsoft |
| `MU.csv` | Micron |
| `NVDA.csv` | NVIDIA |
| `PEP.csv` | PepsiCo (百事) |
| `PG.csv` | Procter & Gamble (宝洁) |
| `QQQ.csv` | Invesco QQQ ETF |
| `SNDK.csv` | SanDisk (闪迪) |
| `SPY.csv` | SPDR S&P 500 ETF |
| `TSLA.csv` | Tesla |
| `TSM.csv` | TSMC (台积电) |
| `WMT.csv` | Walmart (沃尔玛) |

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

### mspd.csv（月度未偿债务结构，307 个月，全量覆盖）

MSPD Table 1（Monthly Statement of the Public Debt）派生：各市场化品种公众持有额
（百万美元，剔除政府间持有）+ 市场化总额 + Bill 占比。

| 列名 | 说明 |
|------|------|
| `BILLS` / `NOTES` / `BONDS` / `TIPS` / `FRN` | 各券种未偿额（百万美元，公众持有口径） |
| `MARKETABLE_TOTAL` | 市场化债务合计 |
| `BILL_SHARE` | Bill 占市场化债务比例（%），>25% 接近债务上限告警 |
| `TOTAL_DEBT` | 总未偿债务（含非市场化与政府间，百万美元；海外官方占比分母） |

### refunding.csv（季度再融资声明 + 融资估算，增量文档库）

`./bin/fetch_refunding` 从 home.treasury.gov 季度再融资页抓取，按 URL 去重增量：

| 列名 | 说明 |
|------|------|
| `id` / `date` / `quarter` | 新闻稿 slug / 发布日期（YYYY-MM-DD）/ 季度（2026-Q3） |
| `kind` | `statement`（Refunding Statement）/ `financing_estimates`（QRA 融资估算） |
| `title` / `url` / `body` | 标题 / 链接 / 全文（关键词 "increase coupon issuance"=偏空、"no change"=偏多） |

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
| 汇率 | DXY、USDJPY（美元兑日元）、USDCNY（美元兑人民币）、EURUSD（欧元/美元）、GBPUSD（英镑/美元）、USDKRW（美元/韩元） |
| 波动率 | MOVE（美林国债期权波动率，债市 VIX） |
| 银行 | KBWB（KBW 银行 ETF，信用页银行系统风险代理） |
| 加密 | BTC、ETH |
| 商品 | WTI、Brent、Gold、Silver、Copper、NG（天然气） |
| 债券 ETF | TLT、IEF（7-10 年）、HYG、LQD |
| 半导体 ETF | SMH、SOXX |
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

## 11.5 分析师目标价 — `data/analyst/`

Nasdaq 100 成分股分析师目标价快照（timsun /assets/equities 面板数据）。
成分股来自 Wikipedia（约月频更新，拉取失败回退本地缓存）；目标价来自 yfinance `Ticker.info`。

| 文件 | 说明 |
|------|------|
| `ndx_components.csv` | 成分股缓存（ticker / company / industry，约 103 只） |
| `ndx_targets.csv` | 长表（date + ticker + price / target_mean / target_high / target_low / analysts / rating），按 (date, ticker) upsert，保留历史 |

> 无分析师覆盖的票跳过；单票拉取失败（含限流）跳过不阻塞整批。
> 已纳入 Actions daily-fetch（`bin/fetch_analyst`）。

---

## 11.7 市场广度 — `data/breadth/`

---

## 12. 财报三张表 — `data/financials/{SYMBOL}/`

来源 yfinance（走 .env SOCKS5 代理；Actions 用 `YF_NO_PROXY=1` 直连）。
`./bin/fetch_financials` 拉取，每只股票 6 张表：`{income|balance|cashflow}_{annual|quarterly}.csv`。
CSV 行 = 报告期末（period end，upsert 模式：同日新值覆盖、新期追加），列 = 科目（yfinance 原始科目名）。
年度近 4 年、季度近 4-8 期；ETF（SPY/QQQ）无财报自动跳过。

---

## 13. SEC 财报原文 — `data/sec/{SYMBOL}/`

来源 SEC EDGAR（免费、无需认证；UA 须为「机构名 + 邮箱」格式）。
`./bin/fetch_sec` 拉取，文件 `{FORM}_{filing_date}.txt.gz`：10-K/10-Q/20-F 正本（跳过 /A 修正件）；
6-K 另配 `--doc-pattern` 按主文档名过滤（如 TSM: `--forms 6-K --doc-pattern '^(tsm-[0-9]{8}x6k|tsm-fs|tsm-revenue)'`），
因 6-K 主文档只是封面页，存**完整提交文本**（含附件），文件名带 accession。
10-K/Q/20-F 为主文档（iXBRL HTML）去 ix:header/标签后的纯文本，gzip 压缩（~20-60KB/份）。
存储模式：文件存在即跳过 → 自然增量；默认回溯 2 年（`--years N` 加长），
仅美股（韩股/ETF 无 EDGAR 申报）。文档直链须用无破折号 accession。

SPX 成分股在均线上方占比（timsun /assets/equities 面板）。
成分股来自 Wikipedia（缓存回退）；yfinance 批量拉 2y 日线逐票算均线。
**与 Barchart $S5TH 交叉验证**：自算 ABV200 vs Barchart 当前值误差 <0.2%。

| 文件 | 说明 |
|------|------|
| `sp500_components.csv` | SPX 成分缓存（ticker / company / category，~503 只） |
| `abv.csv` | 观测日 upsert：ABV50 / ABV100 / ABV200（%，仅派生序列，不存明细） |

> 现成源调研（2026-08-06）：StockCharts $SPXA200R、investing S5TH 均被 Cloudflare 拦截；
> Barchart $S5TH 仅当前值可匿名获取（历史端点 500）。自算为唯一免费完整历史方案。
> 已纳入 Actions daily-fetch（`bin/fetch_breadth`，yfinance 批量 ~2-5 分钟）。

---

## 11.6 跨资产相关性 — `data/cross_asset/`

30 日滚动相关系数（timsun /assets 面板）。纯派生计算（`uv run python -m src.cross_asset`），
依赖资产快照，无网络。仅保留交易日行（周末 BTC/外汇有值、股票无值，相关性按交易日算）。

| 文件 | 说明 |
|------|------|
| `correlation.csv` | 最新相关系数矩阵（13 标的 × 13 标的，覆盖写） |
| `alerts.csv` | 报警标量按日追加：SPX_TLT_30d（股债相关，正=对冲失效）、WTI_SPX_30d（油股相关，负=滞胀交易） |

---

## 12. COT 持仓报告 — `data/cot/cot.csv`

CFTC 官方周度持仓报告（周二数据、周五发布），`./bin/fetch_cot` 拉取（免费，Actions 每日跑，
默认当年+去年全量 upsert）。观测日 = 报告日期。

| 列名 | 说明 |
|------|------|
| `{SYM}_OI` | 总持仓（Open Interest） |
| 商品类（disaggregated）`{SYM}_PROD_L/S`、`{SYM}_SWAP_L/S`、`{SYM}_MM_L/S` | 生产商/商户、掉期商、管理资金（投机）多/空持仓 |
| 金融类（TFF）`{SYM}_DEALER_L/S`、`{SYM}_ASSET_L/S`、`{SYM}_HEDGE_L/S` | 做市商、资管、对冲基金多/空持仓 |

覆盖品种：GC/SI/HG/CL/NG（disaggregated）+ ES/NQ/RTY/ZQ/VX/ZF/ZN/ZB/EUR/JPY（TFF）。
YM（道指）不在 CFTC COT 报告中（2024-26 均无）；DXY（ICE 美元指数）同样不在（2025/26 无）。

## 12.5 FINRA 每日沽空量 — `data/short_selling/finra_daily.csv`

FINRA Reg SHO 每日短卖数据（T+1 免费公开），`./bin/fetch_finra` 拉取（宽表 upsert，观测日 = 交易日）。
直连被 WAF 拦时自动 fallback SOCKS5 代理（Actions 美国 IP 直连）。

| 列名 | 说明 |
|------|------|
| `{SYM}_short_ratio` | ShortVolume / TotalVolume（当日沽空占比） |
| `{SYM}_short_vol` | 当日沽空量（源文件偶见小数，疑 TRF 加权口径，原样存储） |

`--backfill` 探测历史深度并回填（单次最多 400 天，按天幂等可续）；周末/假日 404 自动跳过。

## 12.6 SEC Form 4 内部人交易 — `data/insider/{SYMBOL}.csv`

EDGAR 内部人持股变动（T+2 免费公开），`./bin/fetch_insider` 拉取（长表，accession 去重增量，默认回溯 2 年）。

| 列名 | 说明 |
|------|------|
| `filing_date` / `transaction_date` | 申报日 / 交易执行日 |
| `insider_name` / `title` | 内部人姓名 / 职务（董事标记） |
| `code` | P=公开市场买入 S=公开市场卖出（信号）；A=授予 M=行权 F=缴税代扣 G=赠与 |
| `shares` / `price` / `value` / `shares_after` | 股数 / 价格 / 金额 / 交易后持股 |
| `accession` | EDGAR 申报号（去重键） |

CLI 结尾打印每标的近 90 天 open-market 净买入汇总（仅 P/S 计信号）。

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
