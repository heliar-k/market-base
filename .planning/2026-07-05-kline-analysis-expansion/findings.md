# Findings: K线分析平台 2.0 代码库审查

## 现有架构

### 后端 (FastAPI)
- `src/server.py`: 5694 行，5 个端点
- `/api/symbols` — IBKR 标的列表
- `/api/kline/{symbol}` — OHLCV from CSV
- `/api/diag/{symbol}` — 技术面诊断
- `/api/macro/categories` — FRED 分类
- `/api/macro/{category}` — FRED 时序
- `/api/macro/{category}/term` — 期限结构
- `src/config.py`: 8KB，FRED_SERIES (9 分类), IBKR_SYMBOLS, YF_TICKERS
- `src/macro.py`: 6.2KB，派生指标 SPREAD_2S10S, NET_LIQUIDITY, BEI_5Y/10Y, SOFR_IORB_SPREAD_BP
- `src/indicators.py`: 10.9KB，技术指标 MA/RSI/MACD/BB/SuperTrend/Stoch/CCI/MFI/OBV/ADX
- `src/analyze.py`: 12.8KB，诊断分析 + 评分
- `src/cache.py`: 2KB，Parquet 缓存层

### 前端
- `static/index.html`: 761 行，单文件
- Lightweight Charts 4.2.2 (CDN)
- 侧边栏：技术/宏观切换
- 技术面：K 线蜡烛图 + 成交量 + MA/BB/SuperTrend 叠加 + RSI + MACD
- 宏观面：9 分类可折叠面板，每系列独立折线图，日期筛选 1M-30Y
- 诊断面板：右侧可折叠，对标分析
- 有 tooltip、crosshair 同步、键盘导航

### 数据层
- `data/stocks/`: 13 个股/ETF CSV (AAPL, TSLA, MSFT, NVDA, SPY, QQQ...)
- `data/indices/`: 6 指数 CSV (SPX, IXIC, VIX, RUT, SOX)
- `data/fred/{category}/`: 9 分类周线 CSV
- `data/fed_balance/liquidity.csv`: RRP, TGA, RESERVES, SOFR, IORB, NET_LIQUIDITY
- `data/gex/`: AAPL GEX 快照 × 5
- `data/cache/`: Parquet 缓存
- `data/options/`: 期权数据

### IBKR 集成
- `src/fetchers/ibkr_fetcher.py`: 329 行，ib_insync
- 支持日线 OHLCV 拉取
- 已有 SOCKS5 代理配置路径
- `src/compute_gex.py`: 467 行，IBKR gamma + yfinance OI

## 关键发现

1. **已有 NET_LIQUIDITY 计算** — macro.py 中已实现，fed_balance/liquidity.csv 包含该列
2. **已有 SPREAD_2S10S 计算** — 跨分类派生逻辑已就绪
3. **已有 GEX 数据** — compute_gex.py + data/gex/ 目录
4. **技术指标计算完备** — indicators.py 含 10+ 指标
5. **前端拆分需求** — 761 行单文件即将不可维护
6. **IBKR 只有日线** — 需扩展多周期
7. **FRED 数据只有 9 个分类** — 缺少 WALCL 分项（需补充 fed_balance 管道）
