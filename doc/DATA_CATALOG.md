# 数据目录 (DATA CATALOG)

所有数据以日频增量 CSV 存储，`date` 列统一为首列（ISO 格式），`pd.read_csv(path, index_col='date', parse_dates=True)` 直接可用。

---

## 1. 宏观指标 — `data/fred/fred_series.csv`

34 个系列，来源 FRED API。每日 `./bin/fetch_fred` 更新。

### 波动率
| 列名 | FRED ID | 说明 | 单位 |
|------|---------|------|------|
| `VIX` | VIXCLS | CBOE 波动率指数 | 点 |
| `HY_OAS` | BAMLH0A0HYM2 | 高收益债 OAS 利差 | % |
| `IG_OAS` | BAMLC0A0CM | 投资级债 OAS 利差 | % |

### 通胀预期（市场定价，TIPS 推算）
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `T5YIE` | T5YIE | 5 年盈亏平衡通胀率 |
| `T10YIE` | T10YIE | 10 年盈亏平衡通胀率 |
| `T5YIFR` | T5YIFR | 5Y5Y 远期通胀预期 |

### TIPS 实际收益率（用于自行计算 BEI）
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `DFII5` | DFII5 | 5 年 TIPS 实际收益率 |
| `DFII7` | DFII7 | 7 年 TIPS 实际收益率 |
| `DFII10` | DFII10 | 10 年 TIPS 实际收益率 |
| `DFII20` | DFII20 | 20 年 TIPS 实际收益率 |
| `DFII30` | DFII30 | 30 年 TIPS 实际收益率 |

> **BEI 计算公式**: `BEI_10Y = (DGS10 - DFII10) * 100`（单位 bp）

### 通胀预期（模型/调查，非 TIPS）
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `MICH` | MICH | 密歇根大学消费者 1Y 通胀预期（调查） |
| `EXPINF_1Y` | EXPINF1YR | 克利夫兰联储 1Y 通胀预期（模型） |
| `EXPINF_2Y` | EXPINF2YR | 克利夫兰联储 2Y 通胀预期（模型） |
| `EXPINF_5Y` | EXPINF5YR | 克利夫兰联储 5Y 通胀预期（模型） |
| `EXPINF_10Y` | EXPINF10YR | 克利夫兰联储 10Y 通胀预期（模型） |

### 名义国债收益率曲线
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `DGS1MO` | DGS1MO | 1 月期国债 |
| `DGS3MO` | DGS3MO | 3 月期国债 |
| `DGS6MO` | DGS6MO | 6 月期国债 |
| `DGS1` | DGS1 | 1 年期国债 |
| `DGS2` | DGS2 | 2 年期国债 |
| `DGS3` | DGS3 | 3 年期国债 |
| `DGS5` | DGS5 | 5 年期国债 |
| `DGS7` | DGS7 | 7 年期国债 |
| `DGS10` | DGS10 | 10 年期国债 |
| `DGS20` | DGS20 | 20 年期国债 |
| `DGS30` | DGS30 | 30 年期国债 |

### 金融状况
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `NFCI` | NFCI | 芝加哥联储全国金融状况指数 |

### 美联储流动性
| 列名 | FRED ID | 说明 | 单位 |
|------|---------|------|------|
| `RRPONTSYD` | RRPONTSYD | 隔夜逆回购（RRP） | 十亿美元 |
| `WTREGEN` | WTREGEN | 财政部一般账户（TGA） | 百万美元 |
| `WRESBAL` | WRESBAL | 准备金余额 | 百万美元 |
| `WALCL` | WALCL | 美联储总资产 | 百万美元 |

### 政策利率
| 列名 | FRED ID | 说明 |
|------|---------|------|
| `SOFR` | SOFR | 担保隔夜融资利率 |
| `IORB` | IORB | 准备金余额利率 |

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

# 宏观指标（含国债收益率、通胀预期、流动性）
macro = pd.read_csv('data/fred/fred_series.csv', index_col='date', parse_dates=True)
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
