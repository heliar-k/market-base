# market-base — 项目指南

金融数据管道 + 技术分析工具箱。每日自动拉取美股/指数/宏观/期权/期货数据，支持 K 线技术指标计算、GEX 分析、TUI 双模式应用（技术分析 + 宏观）。

---

> **uv 项目** — 所有 Python 脚本必须用 `uv run` 执行，如 `uv run python -m src.analyze`、`uv run python src/compute_gex.py`。不要直接用 `python`。
> **可安装包** — pyproject.toml 已配置 build-system（hatchling），`uv run` 自动 editable install，`import src.*` 在直接跑脚本 / `-m` / 任意 cwd 下均可用。**禁止** `sys.path.insert` / PYTHONPATH 等路径 hack（import 报错先检查是否用 `uv run`）。
> 数据拉取脚本在 `bin/` 下有 shell 包装（内部已用 `uv run`），直接执行 `./bin/fetch_*` 即可。

## 项目结构

```
market-base/
├── AGENTS.md                     ← 本项目文件
├── pyproject.toml                ← uv 项目配置（>=Python 3.13）
├── .pre-commit-config.yaml       ← ruff + pre-commit hooks
├── .env                          ← FRED_API_KEY（不提交 git）
├── .gitignore
├──
├── src/                         ← Python 源码
│   ├── config.py                 ← 统一配置（FRED 系列、IBKR 品种、yfinance 标的）
│   ├── indicators.py             ← 技术指标计算（MA/RSI/MACD/Bollinger/ADX/Stoch/SuperTrend 等）
│   ├── analyze.py                ← 技术分析诊断引擎（analyze + detect_cdl_hits），CLI 输出 JSON
│   ├── intraday_levels.py        ← 分时价位分析（触及次数/量能分布/插针判定，单日 5m 报告）
│   ├── stock_snapshot.py         ← 盘前实时价 + OI 墙快照（yfinance）
│   ├── volatility_dashboard.py   ← 波动率全景仪表盘分析层（30 指数 + 风险矩阵 + Trade Map + 7 段叙事，规则引擎 + LLM 预留）
│   ├── compute_gex.py            ← Gamma Exposure 与期权墙计算（IBKR + yfinance）
│   ├── cache.py                  ← 指标缓存层（parquet + mtime 失效，TUI 加速）
│   ├── macro.py                  ← 宏观派生指标（2s10s / 净流动性 / BEI / SOFR-IORB）
│   ├── run_fetch.sh              ← 每日全量数据拉取 cron 入口（依次调用所有 ./bin/fetch_*）
│   ├── __init__.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── quality.py            ← DataPoint / QAStatus 数据质量追踪
│   │   ├── _io.py                ← CSV 保存工具（save_daily_csv 快照 + upsert_timeseries 全量）
│   │   ├── ibkr_fetcher.py       ← IBKR 日线 OHLCV（股票 + 指数）
│   │   ├── fred_fetcher.py       ← FRED API 宏观指标（91 个系列，13 分类）
│   │   ├── yfinance_fetcher.py   ← yfinance 资产价格（需 SOCKS5 代理）
│   │   ├── cboe_fetcher.py       ← CBOE 波动率（OVX、VIX 期限结构）
│   │   ├── fsi_fetcher.py        ← OFR 金融压力指数（官方 CSV 全量）
│   │   ├── srf_fetcher.py        ← SRF 使用量（NY Fed Markets API）
│   │   ├── tsy_fetcher.py        ← Treasury 公开市场操作明细（RMP/POMO）
│   │   ├── cfets_fetcher.py      ← CFETS 外汇掉期点（chinamoney）+ Barchart 远期点 + Yahoo
│   │   ├── barchart_client.py    ← Barchart core-api 匿名客户端（XSRF 会话）
│   │   ├── barchart_futures_fetcher.py ← Barchart 期货期限结构（10 品种全合约，IBKR 替代源）
│   │   ├── barchart_options_fetcher.py ← Barchart 期权链（真实 gamma，GEX 降级源）
│   │   ├── barchart_vol_fetcher.py ← Barchart 波动率 30 指数快照（timsun dashboard 对齐源）
│   │   ├── cot_fetcher.py        ← CFTC COT 持仓报告（官方 disaggregated + TFF）
│   │   ├── fed_fetcher.py        ← FOMC 声明 + 官员演讲（federalreserve.gov，增量）
│   │   ├── commodities_fetcher.py← IBKR 商品期货日线（9 个品种，整条曲线）
│   │   └── options_fetcher.py    ← IBKR 期权链参数
│   └── tui/                      ← TUI 应用（Textual 双模式）
│       ├── app.py                ← KlineApp 主入口（技术分析 / 宏观双模式 + Tab 切换）
│       ├── state.py              ← TUI 状态管理（模式、当前标的、回看光标）
│       ├── screens.py            ← 屏幕组装（三栏布局 + 模式切换）
│       └── widgets/              ← 可复用组件（kline_chart / diag_sidebar / macro_chart）
│
├── tests/                        ← pytest 测试套件（157 个测试，tmp_path 隔离 + autouse 清缓存）
│
├── docs/adr/                     ← 架构决策记录（0001 回看交互、0002 重命名 code→src）
│
│
├── bin/                          ← 可执行入口
│   ├── fetch_ibkr
│   ├── fetch_fred
│   ├── fetch_yfinance
│   ├── fetch_cboe
│   ├── fetch_shapiro
│   ├── fetch_sce
│   ├── fetch_fsi
│   ├── fetch_srf
│   ├── fetch_commodities
│   ├── fetch_options
│   ├── fetch_barchart_futures
│   ├── fetch_barchart_vol                ← Barchart 波动率 30 指数快照（timsun dashboard 对齐源）
│   ├── fetch_cot
│   ├── fetch_financials                ← 财报三张表（yfinance 季度+年度，Actions 每日）
│   ├── fetch_sec                       ← SEC 10-K/10-Q/20-F 原文（EDGAR 增量，Actions 每日）
│   ├── fetch_fed                      ← FOMC 声明 + 官员演讲（增量，Actions 每日）
│   ├── fetch_treasury                  ← 国债拍卖（Treasury Fiscal Data API）
│   ├── fetch_bgcr                      ← BGCR 利率（NY Fed Markets API，FRED 无此系列）
│   ├── fetch_acm                       ← NY Fed ACM 10Y 期限溢价（合并进 rates.csv）
│   ├── fetch_refunding                 ← Treasury 季度再融资声明 + QRA 估算（增量）
│   ├── fetch_cgb                       ← 中国国债收益率 10Y/30Y（chinamoney，FRED 无）
│   └── fetch_news                      ← Yahoo Finance 个股新闻（curl_cffi 直连 NCP，不走 yfinance）
│
├── data/                         ← 数据存储（增量 CSV / JSON）
│   ├── fred/{category}/{category}.csv  ← 13 分类 FRED 数据（观测日 upsert；tic= TIC 美债持仓/净买入）
│   ├── cboe/volatility.csv             ← CBOE 波动率（VIX1D, OVX, VIX9D, VIX, VIX3M/6M/1Y, SKEW, VIX_TERM_SLOPE）
│   ├── shapiro/shapiro.csv             ← Shapiro 供需 PCE 分解（观测日 upsert）
│   ├── ofr/fsi.csv                     ← OFR 金融压力指数（观测日 upsert）
│   ├── fred/liquidity/srf.csv          ← SRF 使用量（观测日 upsert）
│   ├── fred/liquidity/tsy_operations.csv ← Treasury 公开市场操作明细（RMP/POMO，覆盖写）
│   ├── fred/liquidity/cfets_swap_points.csv ← CFETS 外汇掉期点（观测日 upsert）
│   ├── sce/sce.csv                     ← NY Fed SCE 通胀预期（观测日 upsert）
│   ├── stocks/{SYMBOL}.csv             ← 10 只股票日线 OHLCV
│   ├── indices/{SYMBOL}.csv            ← 4 个指数日线 OHLCV
│   ├── options/{SYMBOL}_chain.json     ← 期权链参数
│   ├── options/{SYMBOL}_grid.csv       ← 到期日×行权价网格
│   ├── commodities/{SYMBOL}/{SYMBOL}_{YYYYMM}.csv  ← 期货日线
│   ├── gex/{SYMBOL}_greeks_YYYYMMDD.csv  ← Greeks 当日快照（--reuse-greeks 复用）
│   ├── gex/{SYMBOL}_gex_YYYYMMDD_HHMM.csv ← GEX 逐合约明细（每次运行留存）
│   ├── barchart/futures/{ROOT}.csv        ← Barchart 期货全合约曲线（观测日 upsert 宽表）
│   ├── barchart/volatility_snapshot.csv   ← Barchart 波动率 30 指数快照（价格 + 1D/5D/1M/1Y 变化）
│   ├── barchart/commodities/ZQ/ZQ_{YYYYMM}.csv ← Barchart ZQ 合约（rate_expectations 降级）
│   ├── cot/cot.csv                        ← CFTC COT 持仓报告（周频，观测日 upsert）
│   ├── treasury/auction_results.csv       ← 国债拍卖结果全量（~11k 场，覆盖写）
│   ├── treasury/upcoming_auctions.csv     ← 未来拍卖日历（覆盖写）
│   ├── treasury/mspd.csv                  ← 月度未偿债务结构 + Bill 占比（覆盖写）
│   ├── treasury/bill_share_daily.csv      ← 日频 Bill 占比（派生：MSPD 锚+拍卖净发行）
│   ├── treasury/refunding.csv             ← 季度再融资声明 + QRA 融资估算（增量）
│   ├── rate_expectations/                 ← FOMC 概率 + ZQ 快照（每日）
│   ├── financials/{SYMBOL}/              ← 财报三张表 × 年度/季度（period end upsert）
│   ├── sec/{SYMBOL}/{FORM}_{date}.txt.gz ← SEC 10-K/10-Q 原文纯文本（增量，近 2 年）
│   ├── fed/                                ← FOMC 声明 + 官员演讲（federalreserve.gov）
│   │   ├── statements.csv                  ← 声明/纪要/SEP（kind 分类，2020 起，纪要 2021 起）
│   │   └── speeches.csv                    ← 官员演讲（近 2 年）
│   └── cache/{SYMBOL}_indicators.parquet ← 指标缓存（派生产物，mtime 失效）
│
├── static/                       ← Web 前端（FastAPI 静态目录）
│   ├── fed/                         ← 美联储鹰鸽专题页（timsun.net/fed 复刻，4 页）
│   ├── index.html                ← 主仪表盘 SPA
│   ├── js/                       ← 前端 JS（echarts-theme / rates-common 等）
│   ├── rates/                    ← 利率专题页（timsun.net/rates 复刻，6 页）
│   └── volatility/               ← 波动率专题页（timsun.net/volatility 复刻，2 页）
│
└── docs/
    ├── DATA_CATALOG.md           ← 数据目录文档
    ├── TUI_K线分析工具_技术调研.md ← Textual TUI 选型调研
    ├── web-refactor-plan.md      ← Web 重构计划
    └── adr/                      ← 架构决策记录（0001 回看交互、0002 重命名 code→src）
```

---

## 常用命令

### 数据更新方式（重要）

**每日自动（无需本地操作）**：GitHub Actions `daily-fetch` workflow 每个交易日
北京时间 05:00 自动拉取**不依赖 IBKR/TWS** 的数据源并 commit + push，本地 `git pull` 即得：
`fred` / `cboe` / `ofr` / `srf` / `tsy` / `cfets` / `shapiro` / `sce` / `treasury` / `yfinance`（17 品种资产快照）
`barchart_futures` / `barchart_vol` / `cot` / `rate_expectations` / `fed`（Barchart 期货曲线、Barchart 波动率 30 指数快照、CFTC COT、FOMC 概率、FOMC 声明+演讲）
`financials` / `sec`（财报三张表 + SEC 10-K/10-Q 原文）
`minute_bars`（全部股票+指数 1d 日线 + 5m/15m/1h/4h 分钟线，yfinance 原始价与 IBKR 一致；1d 全量历史，5m/15m 深度 60 天、1h/4h 2 年）。

**本地手动（先启动 TWS 或 IB Gateway，端口 4001 实盘 / 4002 模拟）**：只有依赖 IBKR 的才需要本地跑：
`ibkr`（日线，可选——Actions yfinance 已覆盖，IBKR 用于权威覆盖与更深回溯）/ `options` / `commodities` / `index` / `stock` / `rate_expectations`（ZQ 期货来自 commodities）。
日线/分钟线均已由 Actions 用 yfinance 覆盖；本地 IBKR 拉取（`--bar-size all`）只用于补深。

> 别一上来就全部本地拉取——纯 API 部分 Actions 已经跑过了，本地只补 IBKR 部分。
> 手动触发 Actions：`gh workflow run daily-fetch.yml` 或 GitHub Actions 页面点 Run workflow。

```bash
# 数据拉取（bin/ 下的 shell 脚本内部已用 uv run，直接执行即可）
./bin/fetch_ibkr                    # 全部股票 + 指数日线
./bin/fetch_ibkr --symbols SPX,AAPL # 指定品种
./bin/fetch_ibkr --days 365         # 拉取近 365 天
./bin/fetch_fred                    # 全部 FRED 系列（默认 upsert，漏跑自动补）
./bin/fetch_fred --backfill         # 全量覆盖（清旧格式 junk）
./bin/fetch_cboe                    # CBOE 波动率（VIX1D/OVX/VIX9D/VIX/VIX3M/6M/1Y/SKEW/期限结构）
./bin/fetch_shapiro                 # Shapiro 供需 PCE 分解
./bin/fetch_sce                     # NY Fed SCE 通胀预期
./bin/fetch_fsi                     # OFR 金融压力指数
./bin/fetch_srf                     # SRF 使用量
./bin/fetch_tsy                     # Treasury 公开市场操作明细（RMP/POMO）
./bin/fetch_cfets                   # CFETS 外汇掉期点（5 外币对 × 5 期限 + Barchart USDCNH/USDCHF 全期限）
./bin/fetch_barchart_futures        # Barchart 期货期限结构（10 品种全合约，免费匿名）
./bin/fetch_barchart_vol            # Barchart 波动率 30 指数快照（timsun dashboard 对齐源，VXMO/VXEF 唯一源）
./bin/fetch_cot                     # CFTC COT 持仓报告（周频）
./bin/fetch_analyst                  # Nasdaq 100 分析师目标价（Wikipedia 成分 + yfinance）
uv run python -m src.cross_asset     # 跨资产 30 日相关性矩阵（派生，依赖资产快照）
./bin/fetch_fed                     # FOMC 声明 + 官员演讲（增量，首次自动全量）
./bin/fetch_financials               # 财报三张表（yfinance，季度+年度，Actions 每日）
./bin/fetch_sec                      # SEC 10-K/10-Q/20-F 原文（EDGAR 增量，默认回溯 2 年）
./bin/fetch_treasury                # 国债拍卖结果 + 未来日历（全量覆盖）
./bin/fetch_bgcr                     # BGCR 利率（NY Fed，FRED 无；合并进 rates.csv）
./bin/fetch_acm                      # NY Fed ACM 10Y 期限溢价（ACMTP10，合并进 rates.csv）
./bin/fetch_refunding                # Treasury 季度再融资声明 + QRA 融资估算（增量）
./bin/fetch_cgb                      # 中国国债收益率 10Y/30Y（chinamoney 实时曲线）
./bin/fetch_news TSM -n 10          # 个股新闻（直连 Yahoo NCP 接口，绕过 yfinance t.news bug）
./bin/fetch_yfinance                # yfinance 资产价格
./bin/fetch_commodities             # 全部期货（整条曲线）
./bin/fetch_commodities --front-month  # 仅主力合约
./bin/fetch_options                 # 期权链参数

# 分析（src/ 下的 Python 脚本需要用 uv run 执行）
uv run python -m src.analyze                        # 分析 data/MSFT.csv（默认）
uv run python -m src.analyze data/AAPL.csv          # 指定文件
uv run python -m src.analyze data/AAPL.csv --json   # JSON 输出

# TUI（双模式应用）
uv run python -m src.tui.app                        # 启动 TUI（技术分析 + 宏观双模式）

# Web（FastAPI + 利率专题页）
uv run python -m src.server                        # 启动 Web，浏览器打开 localhost:8000
# 静态部署（GitHub Pages，公开仓库）：uv run python -m src.export_pages 生成 site/，
# daily-fetch workflow 每日自动构建部署 → https://heliar-k.github.io/market-base/
# 限制：K 线仅近 3 年、相关性页仅近 5 年、诊断面板无光标回看（静态预渲染的固有降级）
# 利率专题（timsun.net/rates 复刻）：/rates/ 入口页 → 联邦基金/收益率曲线/拍卖/实际利率/利率预期
# 研判由 src/rates_analysis.py 规则引擎生成，LLM 接入点：generate_analysis() → _llm_generate()

# 测试
uv run python -m pytest                            # 全量测试（157 个）

# GEX 计算（IBKR 优先，拿不到 Greeks 自动降级 yfinance）
uv run python src/compute_gex.py                        # AAPL（默认）
uv run python src/compute_gex.py --symbol TSM          # 指定品种
uv run python src/compute_gex.py --expirations 6        # 6 个到期月

# GEX 常用组合：实盘 4001（自动 readonly）+ 大批量 + 当日快照复用
uv run python src/compute_gex.py --symbol MSFT --port 4001 --batch-size 50  # 首次拉取（~35s），存当日 Greeks 快照
uv run python src/compute_gex.py --symbol MSFT --reuse-greeks               # 当天重跑（~3s），只刷 OI；spot 动 1%+ 需重新拉快照
# 盘前时段 IBKR 不推期权行情（Error 10091）→ 自动降级 yfinance IV 反推 BS gamma（OI 真实、结果可用，精度低于 IBKR）

# 保护结构报价（put / 价差 / 领口成本对比）
uv run python src/hedge_planner.py --symbol TSM

# Sell Put 选点位（期权墙 + 技术面交叉；默认复用当日 GEX 数据，--fetch 强制重拉）
uv run python src/sell_put.py --symbol TSM

# cron（每个交易日美股收盘后，北京时间 05:00）
# 0 5 * * 1-5 cd /Users/guankai/Documents/K线分析 && bash src/run_fetch.sh >> logs/cron.log 2>&1
```

---

## 关键设计决策

### 1. 数据层 vs 分析层分离
- **fetchers/** 只负责拉取数据、写入 CSV，**不**包含分析逻辑
- **indicators.py / analyze.py** 只负责读取本地 CSV、计算指标、输出报告
- **指标归位规则**：输出是"逐日一行的新列"（df→df+列，可缓存）→ `indicators.py` 的 `add_*()`；输出是"给人看的报告"（文本/CLI）→ 独立模块（如 `intraday_levels.py` 分钟线、`compute_gex.py` 期权），不进指标缓存
- 新增 fetcher → 在 `src/fetchers/` 下新建文件；宏观 fetcher 实现 `fetch_*() -> DataFrame`（全量 upsert），其余按需用 DataPoint
- 新增指标 → 在 `src/indicators.py` 里加 `add_*()` 函数，并在 `compute_all_indicators()` 注册

### 2. CSV 存储模式（双轨）

**宏观时间序列**（FRED / Shapiro / SCE / CBOE）— 观测日为 key，全量 upsert：
- `_io.py` 的 `upsert_timeseries()` 按观测日合并：同日新值覆盖旧值，新日追加，缺失保留旧值
- 每次 `./bin/fetch_*` 都拉源全量历史并 upsert → **忘记运行自动补漏**，无需检测逻辑
- `--backfill` 全量覆盖（清旧格式 junk）；默认即 upsert
- 源本就是全量历史，upsert 零额外拉取成本

**日频快照**（yfinance 资产价格）— 拉取日为 key，每日追加：
- `_io.py` 的 `save_daily_csv()` 负责去重写入：同日期行会被覆盖

- 所有 CSV `date` 列为首列（ISO 格式）
- 读数据的标准模式：`pd.read_csv(path, index_col='date', parse_dates=True)`

### 3. 数据质量追踪
- 每个指标拉取返回 `DataPoint`（含 value / as_of / source / qa_status）
- 失败的数据点用 `mark_error()` 标记，不写入 CSV，不影响已有数据
- 见 `src/fetchers/quality.py`

### 4. 统一配置
- `src/config.py` 是唯一配置入口：FRED 系列、IBKR 品种、yfinance 标的
- `.env` 管理密钥（`FRED_API_KEY`），`config.py` 自动加载
- ib-insync TWS 端口用纸交易模式 `4002`（实盘 `4001`）

### 5. yfinance 需要 SOCKS5 代理
- `https_proxy=socks5://127.0.0.1:7890` 在 `.env` 中配置
- 代理必须在导入 yfinance **之前**设置环境变量（`yfinance_fetcher.py` 在 import 前设置）

### 6. IBKR 端口与限制
- 端口：`4002` = 模拟账户（默认，行情订阅数有限 ~3-5，GEX 用 `--batch-size 3` 小批量串行）；`4001` = 实盘只读（`connect_ib` 自动带 `readonly=True`，可 `--batch-size 50`）
- `ibkr_fetcher.py` 连接时依次尝试 4002 → 4001（4001 按只读连接）
- Greeks 当日快照：`data/gex/{SYMBOL}_greeks_YYYYMMDD.csv`，`--reuse-greeks` 复用后只拉 yfinance OI；gamma 贴近墙位对 spot 敏感，spot 动 1%+ 要重拉
- gamma 符号惯例：call 正 / put 负（IBKR modelGreeks 恒非负，`fetch_options_greeks` 里统一翻转，与 `greeks_from_yf` 一致）
- 每次请求后 `sleep(15s)` 避免限流（配置在 `config.ibkr.request_delay_seconds`）

---

## 编码约定

- **语言**: 注释和文档用中文（面向中文用户），代码标识符用英文
- **格式化**: ruff (select E/F/I/W) + ruff-format，`pre-commit` 在 git commit 时自动执行（`ruff --fix` + `ruff-format` 自动修并重新暂存）。**写完代码无需手动跑 ruff/pre-commit**，只验证功能正确性（代码能跑）即可；E501（行太长）不会被自动修，commit 被拦时再手动改
- **类型提示**: 所有函数签名带类型注解，用 `|` 替代 `Optional`（Python 3.10+）
- **import**: 先标准库 → 第三方 → `src.*`（`isort` 自动处理）
- **测试**: pytest 测试套件（`tests/`，157 个测试），用 `tmp_path` 隔离 + autouse fixture 清理缓存。运行 `uv run python -m pytest`

---

## 常用的 pi 技能

| 场景 | 技能 | 说明 |
|------|------|------|
| 新 fetcher | `python-patterns` | 宏观 fetcher 用 upsert_timeseries 全量模式 |
| 调试 bug | `diagnosing-bugs` | 定位数据不更新、指标计算错误等问题 |
| 查 yfinance 用法 | `find-docs` | 获取 yfinance / pandas / ib_insync 的 API 文档 |
| 指标代码审查 | `ponytail-review` | 检查是否过度抽象、引入不必要依赖 |
| 分析特定股票 | `yfinance-data` | 拉取实时行情、基本面数据 |
| 盘前复核/交易计划 | `planning-trades` | 做空/做多挂单计划、盘前实时价（`uv run python -m src.stock_snapshot {X} --oi`）、GEX 验证 |
| 期权分析 | `options-payoff` | 可视化期权盈亏曲线 |
| 财报前瞻/复盘 | `earnings-preview` / `earnings-recap` | 财报前预期简报 / 财报后结果与股价反应分析 |
| 分析师预期趋势 | `estimate-analysis` | EPS/营收预期修正趋势追踪 |
| 写 commit | `git-commit` | 生成规范的 commit message |

---

## 关键 API 速查

### 数据质量

```python
from src.fetchers.quality import DataPoint, QAStatus

dp = DataPoint(metric="CPI", source="FRED / CPIAUCSL", formula="YoY %")
dp.value = 3.2
dp.as_of = "2025-06-01"
dp.mark_ok()   # QAStatus.OK
dp.mark_error("API timeout")  # QAStatus.ERROR
```

### 指标计算

```python
from src.indicators import load_data, compute_all_indicators

df = load_data("data/AAPL.csv")
df = compute_all_indicators(df)  # 返回带所有指标列的 DataFrame
# 列: MA5/10/20/60/120, RSI, MACD/MACD_hist/MACD_signal,
#      BB_lower/BB_mid/BB_upper, ATR, vol_MA20/vol_ratio,
#      ADX/DMP/DMN, STOCH_k/STOCH_d, SUPERT/SUPERT_dir,
#      OBV, CCI, MFI, CDL_* (62 种 K 线形态)
```

### 配置扩展

```python
# 在 src/config.py 中：
# - FRED_SERIES: 新增分类 {category: {metric: series_id}}
# - YF_TICKERS: 新增 yfinance 标的
# - IBKR_SYMBOLS: 新增 IBKR 品种
# - COMMODITY_FUTURES: 新增期货品种
```

---

## 自定义 Agent 定义

### agent: 分析助手

分析指定股票/指数的技术面，生成综合评分报告。

> 注意：所有 Python 脚本执行必须用 `uv run` 前缀。

```yaml
---
name: 分析助手
description: 对指定股票或指数运行技术分析，输出 Rich 格式的评分报告。适用于 ask about a stock, 分析 AAPL, 看看 MSFT 的技术面。
tools: read, bash, write
thinking: medium
---

1. 确定品种名（从用户输入提取，如 AAPL、MSFT、SPX）
2. 检查 `data/stocks/{SYMBOL}.csv` 或 `data/indices/{SYMBOL}.csv` 是否存在
3. 运行 `uv run python -m src.analyze data/{type}/{SYMBOL}.csv`
4. 如果文件不存在，提示用户先用 `./bin/fetch_ibkr --symbols {SYMBOL}` 拉取
5. 返回分析结果，解读关键指标信号
```

### agent: 数据维护

管理数据管道的日常运维：拉取、检查、修复数据。

> 优先用 `./bin/fetch_*` 脚本（内部已用 uv run）；直接跑 Python 文件时加 `uv run` 前缀。

```yaml
---
name: 数据维护
description: 执行数据拉取、检查数据完整性、处理缺失数据。适用于 update data, 拉取数据, 检查数据完整性。
tools: read, bash, write
thinking: low
---

1. 确认用户想拉取的数据范围（全部 / FRED / IBKR / CBOE / yfinance / 期权 / 期货）
2. 执行对应 `./bin/fetch_*` 脚本
3. 检查数据目录中最新的日期是否更新到当天/前一个交易日
4. 如遇连接失败（TWS 未启动 / API 超时），给出明确的修复步骤
5. 报告本次更新概况
```

### agent: 配置修改

修改项目配置：新增/删除数据品种、调整参数。

> shell 脚本走 `./bin/`，Python 脚本走 `uv run python`。

```yaml
---
name: 配置修改
description: 修改 FRED 系列、IBKR 品种、yfinance 标的、或运行参数。适用于 add new stock, 新增 FRED 系列。
tools: read, bash, edit, write
thinking: low
---

1. 读 `src/config.py` 了解当前配置结构
2. 确定修改范围：
   - FRED 系列 → 修改 `FRED_SERIES` 字典，按分类添加
   - IBKR 品种 → 修改 `IBKR_SYMBOLS` 列表
   - yfinance 标的 → 修改 `YF_TICKERS` 字典
   - 期货品种 → 修改 `COMMODITY_FUTURES` 字典
   - 运行参数 → 修改 `IbkrConfig` / `Config` dataclass
3. 同步更新 `docs/DATA_CATALOG.md` 中的表格
4. 新增品种时确保数据目录存在
```

### agent: 期权分析

运行 GEX 计算，分析期权墙和 Gamma Flip 点。

> 必须用 `uv run python src/compute_gex.py` 执行。

```yaml
---
name: 期权分析
description: 计算 Gamma Exposure 和期权墙，识别支撑/阻力位。适用于 gex, 期权墙, gamma exposure, 计算 GEX。
tools: read, bash, write
thinking: medium
---

1. 确认品种（默认 AAPL）
2. 检查 TWS/IB Gateway 端口：4001（实盘只读，快）或 4002（模拟，慢）
3. 运行 `uv run python src/compute_gex.py --symbol {SYMBOL} --port 4001 --batch-size 50`；当天重跑加 `--reuse-greeks`
4. 解读结果：
   - 最大正 GEX 行权价 = dealer 做多 gamma → 支撑位
   - 最大负 GEX 行权价 = dealer 做空 gamma → 阻力位
   - Gamma Flip 区域 = GEX 由正转负的过渡区间
   - 净 GEX > 0 → 市场趋于稳定；净 GEX < 0 → 波动可能放大
```

### agent: 期货分析

查看期货曲线结构、主力合约价差、期限结构。

> 数据拉取走 `./bin/fetch_commodities`，分析脚本走 `uv run python`。

```yaml
---
name: 期货分析
description: 分析商品期货各合约数据，查看期货曲线。适用于 futures, 期货曲线, 查看期货。
tools: read, bash
thinking: medium
---

1. 确定品种（如 GC、CL、ES）
2. 列出 `data/commodities/{SYMBOL}/` 下的所有 CSV 文件
3. 读取各合约的最新收盘价，展示期限结构
4. 计算主力合约（最近月）和次主力合约的价差
5. 如数据不存在，提示先用 `./bin/fetch_commodities --symbols {SYMBOL}` 拉取
```
