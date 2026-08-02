# 数据目录 (DATA CATALOG)

所有数据以 CSV 存储，`date` 列统一为首列（ISO 格式），`pd.read_csv(path, index_col='date', parse_dates=True)` 直接可用。

宏观时间序列（FRED / Shapiro / SCE / CBOE）以**观测日**为 key，每次 `./bin/fetch_*` 拉源全量历史并 upsert（同日新值覆盖旧值，新日追加）→ 忘记运行自动补漏。股票/指数/期货以交易日为 key 增量追加。

---

## 1. 宏观指标 — `data/fred/{category}/` （12 分类）

12 个分类、65 个系列，来源 FRED API。每次 `./bin/fetch_fred` 拉全量历史并 upsert（漏跑自动补）；`--backfill` 全量覆盖。

| 分类 | 路径 | 系列 | 内容 |
|------|------|------|------|
| `volatility` | `data/fred/volatility/volatility.csv` | 3 | VIX / HY_OAS / IG_OAS |
| `inflation` | `data/fred/inflation/inflation.csv` | 19 | CPI / PCE / 核心 / CPI细分 / Super-core / BEI / 通胀预期 |
| `labor` | `data/fred/labor/labor.csv` | 3 | 失业率 / 非农 / 首申失业金 |
| `growth` | `data/fred/growth/growth.csv` | 5 | 实际GDP / 工业产出 / 实际PCE / 产能利用率 / 制造业新订单 |
| `rates` | `data/fred/rates/rates.csv` | 14 | 联邦基金利率 / SOFR / IORB / 国债全期限 |
| `tips` | `data/fred/tips/tips.csv` | 5 | 5Y-30Y TIPS 实际收益率 |
| `liquidity` | `data/fred/liquidity/liquidity.csv` | 5 | NFCI / 准备金 / RRP / TGA / 联储总资产 |
| `sentiment` | `data/fred/sentiment/sentiment.csv` | 2 | 消费者信心 / 金融压力指数 |
| `fx` | `data/fred/fx/fx.csv` | 1 | 贸易加权美元指数 |
| `producer_prices` | `data/fred/producer_prices/producer_prices.csv` | 4 | PPI Final Demand / 核心PPI / 分项 |
| `consumption` | `data/fred/consumption/consumption.csv` | 1 | 个人储蓄率 |
| `labor_market` | `data/fred/labor_market/labor_market.csv` | 4 | JOLTS 职位空缺/离职率 + 失业人数 + ECI 工资 |

### 列名速查
`volatility`: VIX, HY_OAS, IG_OAS
`inflation`: CPI, PCE, CORE_CPI, CORE_PCE, CPI_SHELTER, CPI_FOOD, CPI_ENERGY, CORE_SERVICES, CORE_GOODS, SUPERCORE_PCE, SUPERCORE_PCE_REAL, T5YIE, T10YIE, T5YIFR, MICH, EXPINF_1Y, EXPINF_2Y, EXPINF_5Y, EXPINF_10Y
`labor`: UNRATE, PAYEMS, ICSA
`growth`: GDP, INDPRO, REAL_PCE, CAPU, DGORDER
`rates`: FEDFUNDS, SOFR, IORB, DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DGS20, DGS30
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
VIX9D/VIX 全量序列一并落盘，使 VIX_TERM_SLOPE 可复算审计。

| 列名 | 说明 |
|------|------|
| `OVX` | CBOE 原油波动率指数 |
| `VIX9D` | 9 天 VIX |
| `VIX` | 30 天 VIX（CBOE 原始序列；FRED VIXCLS 也有） |
| `VIX_TERM_SLOPE` | VIX 期限结构斜率（VIX - VIX9D），正=contango，负=backwardation |

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
| `high_yield` | 中标利率（%） |
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
