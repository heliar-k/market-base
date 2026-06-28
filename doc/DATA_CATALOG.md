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

## 2. 国债收益率 — `data/treasury/treasury_yields.csv`

11 个期限，来源美国财政部日频 XML。每日 `./bin/fetch_treasury` 更新。

| 列名 | 说明 |
|------|------|
| `DGS1MO` | 1 月期 |
| `DGS3MO` | 3 月期 |
| `DGS6MO` | 6 月期 |
| `DGS1` | 1 年期 |
| `DGS2` | 2 年期 |
| `DGS3` | 3 年期 |
| `DGS5` | 5 年期 |
| `DGS7` | 7 年期 |
| `DGS10` | 10 年期 |
| `DGS20` | 20 年期 |
| `DGS30` | 30 年期 |

---

## 3. 波动率 — `data/cboe/volatility.csv`

来源 CBOE CDN + FRED。每日 `./bin/fetch_cboe` 更新。

| 列名 | 说明 |
|------|------|
| `VIX` | CBOE 标普 500 波动率指数 |
| `OVX` | CBOE 原油波动率指数 |
| `VIX_TERM_SLOPE` | VIX 期限结构斜率（VIX - VIX9D，bp） |

---

## 4. 流动性 — `data/fed_balance/liquidity.csv`

来源 FRED，包含派生的净流动性指标。每日 `./bin/fetch_fed_balance` 更新。

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

## 5. 股票日线 — `data/stocks/{SYMBOL}.csv`

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

## 6. 指数日线 — `data/indices/{SYMBOL}.csv`

4 个指数，来源 IBKR。格式同上 OHLCV。

| 文件 | 品种 |
|------|------|
| `SPX.csv` | S&P 500 指数 |
| `IXIC.csv` | 纳斯达克综合指数 |
| `RUT.csv` | Russell 2000 |
| `VIX.csv` | CBOE VIX 指数 |

---

## 7. 期权链 — `data/options/`

10 只品种的期权链参数。

| 文件 | 内容 |
|------|------|
| `{SYMBOL}_chain.json` | 完整参数（到期日、行权价、交易所） |
| `{SYMBOL}_grid.csv` | 到期日 × 行权价格网 |

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

# 宏观指标
macro = pd.read_csv('data/fred/fred_series.csv', index_col='date', parse_dates=True)
# 算 5Y BEI
macro['BEI_5Y'] = (macro['DGS5'] - macro['DFII5']) * 100

# 国债收益率
tsy = pd.read_csv('data/treasury/treasury_yields.csv', index_col='date', parse_dates=True)
# 算 2s10s 利差
tsy['2s10s'] = tsy['DGS10'] - tsy['DGS2']

# 流动性
liq = pd.read_csv('data/fed_balance/liquidity.csv', index_col='date', parse_dates=True)

# 股票 K 线
aapl = pd.read_csv('data/stocks/AAPL.csv', index_col='date', parse_dates=True)

# 波动率
vol = pd.read_csv('data/cboe/volatility.csv', index_col='date', parse_dates=True)
```
