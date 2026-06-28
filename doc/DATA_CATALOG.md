# 数据目录 (DATA CATALOG)

所有数据以日频增量 CSV 存储，`date` 列统一为首列（ISO 格式），`pd.read_csv(path, index_col='date', parse_dates=True)` 直接可用。

---

## 1. 宏观指标 — `data/fred/{category}/` （9 分类）

9 个分类、46 个系列，来源 FRED API。每日 `./bin/fetch_fred` 更新。

| 分类 | 路径 | 系列 | 内容 |
|------|------|------|------|
| `volatility` | `data/fred/volatility/volatility.csv` | 3 | VIX / HY_OAS / IG_OAS |
| `inflation` | `data/fred/inflation/inflation.csv` | 11 | CPI / PCE / 核心CPI / BEI / 通胀预期 |
| `labor` | `data/fred/labor/labor.csv` | 3 | 失业率 / 非农 / 首申失业金 |
| `growth` | `data/fred/growth/growth.csv` | 2 | 实际GDP / 工业产出 |
| `rates` | `data/fred/rates/rates.csv` | 14 | 联邦基金利率 / SOFR / IORB / 国债全期限 |
| `tips` | `data/fred/tips/tips.csv` | 5 | 5Y-30Y TIPS 实际收益率 |
| `liquidity` | `data/fred/liquidity/liquidity.csv` | 5 | NFCI / 准备金 / RRP / TGA / 联储总资产 |
| `sentiment` | `data/fred/sentiment/sentiment.csv` | 2 | 消费者信心 / 金融压力指数 |
| `fx` | `data/fred/fx/fx.csv` | 1 | 贸易加权美元指数 |

### 列名速查
`volatility`: VIX, HY_OAS, IG_OAS
`inflation`: CPI, PCE, CORE_CPI, T5YIE, T10YIE, T5YIFR, MICH, EXPINF_1Y, EXPINF_2Y, EXPINF_5Y, EXPINF_10Y
`labor`: UNRATE, PAYEMS, ICSA
`growth`: GDP, INDPRO
`rates`: FEDFUNDS, SOFR, IORB, DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DGS20, DGS30
`tips`: DFII5, DFII7, DFII10, DFII20, DFII30
`liquidity`: NFCI, RRPONTSYD, WTREGEN, WRESBAL, WALCL
`sentiment`: UMCSENT, STLFSI4
`fx`: DXY

---

## 2. 波动率 — `data/cboe/volatility.csv`

来源 CBOE CDN。每日 `./bin/fetch_cboe` 更新。
VIX 不在此文件（已在 FRED fred_series.csv），避免重复。

| 列名 | 说明 |
|------|------|
| `OVX` | CBOE 原油波动率指数 |
| `VIX_TERM_SLOPE` | VIX 期限结构斜率（VIX - VIX9D），正=contango，负=backwardation |

---

## 3. 流动性 — `data/fed_balance/liquidity.csv`

读取 FRED CSV 计算派生指标，不重复请求 API。每日 `./bin/fetch_fed_balance` 更新。

| 列名 | 说明 | 单位 |
|------|------|------|
| `RRP` | 隔夜逆回购 | 百万美元 |
| `TGA` | 财政部一般账户 | 百万美元 |
| `RESERVES` | 准备金余额 | 百万美元 |
| `SOFR` | SOFR 利率 | % |
| `IORB` | IORB 利率 | % |
| `SOFR_IORB_SPREAD` | SOFR - IORB 利差 | bp |
| `NET_LIQUIDITY` | 净流动性 = WALCL - RRP - TGA | 百万美元 |

---

## 4. 股票日线 — `data/stocks/{SYMBOL}.csv`

10 只股票，来源 IBKR。每日 `./bin/fetch_ibkr` 更新。

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
| `average` | 均价（VWAP） |
| `barCount` | 日内棒数 |

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

9 个期货品种，来源 IBKR，自动拉取全部未过期合约。每日 `./bin/fetch_commodities` 更新。

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

每个合约月一个 CSV，OHLCV 格式同股票/指数。

### Grid CSV 列格式

| 列名 | 说明 |
|------|------|
| `expiry` | 到期日（YYYY-MM-DD） |
| `strike` | 行权价（float） |
| `exchange` | 交易所（如 SMART、CBOE） |
| `trading_class` | 期权类别（如 AAPL、TSLA） |

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

# 流动性（派生指标）
liq = pd.read_csv('data/fed_balance/liquidity.csv', index_col='date', parse_dates=True)

# 波动率
vol = pd.read_csv('data/cboe/volatility.csv', index_col='date', parse_dates=True)

# 股票 K 线
aapl = pd.read_csv('data/stocks/AAPL.csv', index_col='date', parse_dates=True)

# 指数日线
spx = pd.read_csv('data/indices/SPX.csv', index_col='date', parse_dates=True)
```
