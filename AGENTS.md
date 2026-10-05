# market-base — 项目指南

> 站名「观澜台」（US Macro 研究平台）：网页品牌与页面 title 已统一为观澜台，仓库名仍为 market-base。

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
│   ├── analysis_utils.py         ← 分析层共享工具（read_csv_or_empty / chg_prev / chg_pct / zone / 分位口径）
│   ├── intraday_levels.py        ← 分时价位分析（触及次数/量能分布/插针判定，单日 5m 报告）
│   ├── stock_snapshot.py         ← 盘前实时价 + OI 墙快照（yfinance）
│   ├── volatility_dashboard.py   ← 波动率全景仪表盘分析层（30 指数 + 风险矩阵 + Trade Map + 7 段叙事，规则引擎 + LLM 预留）
│   ├── compute_gex.py            ← Gamma Exposure 与期权墙计算（IBKR + yfinance）
│   ├── cache.py                  ← 指标缓存层（parquet + mtime 失效，TUI 加速）
│   ├── macro.py                  ← 宏观派生指标（2s10s / 净流动性 / BEI / SOFR-IORB）
│   ├── bill_share.py             ← 日频 Bill 占比（MSPD 锚 + 拍卖净发行派生）
│   ├── pricing.py                ← 定价收敛点（compute_gex / sell_put / hedge_planner 三处重复的 BS 定价收敛）
│   ├── options_structure.py      ← 期权结构快照分析（GEX/DEX/Vanna/Charm 22 标的面板）
│   ├── cross_asset.py            ← 跨资产相关性面板数据（22 标的 30 日矩阵 + 4 结构报警对，派生，依赖资产快照）
│   ├── sell_put.py               ← Sell Put 选点位（期权墙 + 技术面交叉）
│   ├── hedge_planner.py          ← 下跌保护结构报价器（put / 价差 / 领口）
│   ├── server.py                 ← FastAPI Web 后端（45 routes，组合根：import 全部分析层）
│   ├── export_pages.py           ← 静态站点导出（API JSON 预渲染 → frontend/public/api/）
│   ├── *_analysis.py             ← 专题分析引擎（9 个，规则引擎生成叙事，只读 CSV 不写盘）：
│   │     rates / credit / inflation / labor / treasury / fed / volatility /
│   │     assets / liquidity —— 读 CSV 统一走 src/analysis_utils.py
│   ├── run_fetch.sh              ← 每日全量数据拉取 cron 入口（依次调用所有 ./bin/fetch_*）
│   ├── __init__.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── quality.py            ← DataPoint / QAStatus 数据质量追踪
│   │   ├── _io.py                ← CSV 保存工具（save_daily_csv 快照 + upsert_timeseries 全量）
│   │   ├── _symbol_fetch.py      ← 股票/指数日线拉取统一编排（IBKR 优先，yfinance 回退）
│   │   ├── _wiki.py              ← Wikipedia 成分股表解析（NDX / S&P 500 共用）
│   │   ├── jina_reader.py        ← Jina Reader 通用抓取（r.jina.ai 网页→Markdown，绕 Cloudflare）
│   │   ├── ibkr_fetcher.py       ← IBKR 日线 OHLCV（股票 + 指数）
│   │   ├── fred_fetcher.py       ← FRED API 宏观指标（91 个系列，13 分类）
│   │   ├── yfinance_fetcher.py   ← yfinance 资产价格（需 SOCKS5 代理）
│   │   ├── cboe_fetcher.py       ← CBOE 波动率（OVX、VIX 期限结构）
│   │   ├── fsi_fetcher.py        ← OFR 金融压力指数（官方 CSV 全量）
│   │   ├── srf_fetcher.py        ← SRF 使用量（NY Fed Markets API）
│   │   ├── tsy_fetcher.py        ← Treasury 公开市场操作明细（RMP/POMO）
│   │   ├── cfets_fetcher.py      ← CFETS 外汇掉期点（chinamoney）+ Barchart 远期点 + Yahoo
│   │   ├── barchart_client.py    ← Barchart core-api 客户端（直连失败自动降级无头浏览器过 AWS WAF）
│   │   ├── barchart_futures_fetcher.py ← Barchart 期货期限结构（10 品种全合约，IBKR 替代源）
│   │   ├── barchart_options_fetcher.py ← Barchart 期权链（真实 gamma，GEX 降级源）
│   │   ├── barchart_vol_fetcher.py ← Barchart 波动率 30 指数快照（timsun dashboard 对齐源）
│   │   ├── cot_fetcher.py        ← CFTC COT 持仓报告（官方 disaggregated + TFF）
│   │   ├── fed_fetcher.py        ← FOMC 声明 + 官员演讲（federalreserve.gov，增量）
│   │   ├── commodities_fetcher.py← IBKR 商品期货日线（9 个品种，整条曲线）
│   │   ├── options_fetcher.py    ← IBKR 期权链参数
│   │   ├── breadth_fetcher.py    ← 市场广度 ABV（SPX 成分股在均线上方占比，Wikipedia 成分）
│   │   ├── dts_fetcher.py        ← Treasury Daily Statement（DTS 现金流，Fiscal Data API）
│   │   ├── finra_fetcher.py      ← FINRA Reg SHO 日度卖空量
│   │   ├── insider_fetcher.py    ← SEC Form 4 内部人交易（EDGAR）
│   │   ├── analyst_fetcher.py    ← Nasdaq 100 分析师目标价快照（Wikipedia 成分 + yfinance）
│   │   ├── sec_fetcher.py        ← SEC 10-K/10-Q 原文（EDGAR 增量）
│   │   ├── etf_fetcher.py        ← ETF 数据管线（全量清单 + 精选池日线，timsun 源）
│   │   ├── fx_fetcher.py         ← 外汇对日线 16 对宽表（timsun /assets/fx）
│   │   ├── acm_fetcher.py        ← NY Fed ACM 10Y 期限溢价（合并进 rates.csv）
│   │   ├── bgcr_fetcher.py       ← BGCR 利率（NY Fed Markets API）
│   │   ├── cgb_fetcher.py        ← 中国国债 10Y/30Y（chinamoney）
│   │   ├── cme_futures_fetcher.py  ← CME BTC 期货仓位快照（官方 Settlements API）
│   │   ├── coinglass_fetcher.py  ← Coinglass 全市场聚合快照（经 Jina）
│   │   ├── crypto_basis_fetcher.py ← CME BTC 基差日序列（Yahoo BTC=F）
│   │   ├── crypto_derivatives_fetcher.py ← 加密衍生品快照（OKX + Deribit + CME）
│   │   ├── etf_flows_fetcher.py  ← BTC 现货 ETF 资金流（Farside via Jina）
│   │   ├── financials_fetcher.py ← 财报三张表（yfinance 季度+年度）
│   │   ├── news_fetcher.py       ← Yahoo Finance 个股新闻（curl_cffi 直连 NCP）
│   │   ├── rate_expectations_fetcher.py ← FOMC 概率（ZQ 期货隐含）
│   │   ├── refunding_fetcher.py  ← Treasury 季度再融资声明 + QRA 估算
│   │   └── treasury_fetcher.py   ← 国债拍卖 + 债务数据（Treasury Fiscal Data API）
│   └── tui/                      ← TUI 应用（Textual 双模式）
│       ├── app.py                ← KlineApp 主入口（技术分析 / 宏观双模式 + Tab 切换）
│       ├── state.py              ← TUI 状态管理（模式、当前标的、回看光标）
│       ├── screens.py            ← 屏幕组装（三栏布局 + 模式切换）
│       └── widgets/              ← 可复用组件（kline_chart / diag_sidebar / macro_chart / _plot_common）
│
├── tests/                        ← pytest 测试套件（707 个测试，tmp_path 隔离 + autouse 清缓存）
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
│   ├── fetch_fx                        ← 外汇对日线 16 对（timsun /assets/fx 数据源）
│   ├── fetch_etf                       ← ETF 全量清单 + 精选池 37 只日线（timsun /assets/etfs）
│   ├── fetch_options_structure         ← 22 标的期权结构快照（GEX/DEX/Vanna/Charm，yfinance 降级源）
│   ├── fetch_crypto_derivatives        ← 加密衍生品快照（OKX + Deribit + CME 基差）
│   ├── fetch_crypto_basis               ← CME BTC 基差日序列（Yahoo BTC=F，timsun V1 治理）
│   ├── fetch_coinglass                  ← Coinglass 全市场聚合快照（经 Jina Reader）
│   ├── fetch_cme_futures               ← CME BTC 期货仓位快照（Settlements API，curl_cffi/Jina）
│   ├── fetch_polymarket                 ← Polymarket 预测市场监测（gamma-api 直连免 key）
│   ├── fetch_etf_flows                 ← BTC 现货 ETF 资金流（Farside via Jina Reader）
│   ├── fetch_breadth                   ← 市场广度 ABV（SPX 成分股在均线上方占比）
│   ├── fetch_dts                       ← Treasury Daily Statement 现金流（Fiscal Data API）
│   ├── fetch_finra                     ← FINRA Reg SHO 日度卖空量
│   ├── fetch_insider                   ← SEC Form 4 内部人交易（EDGAR）
│   ├── fetch_analyst                    ← Nasdaq 100 分析师目标价（Wikipedia 成分 + yfinance）
│   ├── fetch_index                     ← 指数日线（_symbol_fetch 统一编排）
│   ├── fetch_stock                     ← 股票日线（_symbol_fetch 统一编排）
│   ├── fetch_rate_expectations         ← FOMC 概率 + ZQ 快照（每日）
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
│   ├── fx/fx_pairs.csv                  ← 外汇对日线宽表 16 对（timsun /assets/fx）
│   ├── etf/universe.csv                 ← Nasdaq Trader 全量 ETF 清单（~5600 只）+ 分类
│   ├── etf/pool_prices.csv              ← 精选池 37 只 ETF 日线（timsun /assets/etfs）
│   ├── options_structure/{date}.json    ← 22 标的期权结构快照（GEX/DEX/Vanna/Charm）
│   ├── crypto_derivatives/{date}.json   ← 加密衍生品快照（OKX/Deribit/CME）
│   ├── crypto_basis/basis.csv          ← CME BTC 基差日序列（观测日 upsert，治理后 ~35% 完整）
│   ├── coinglass/{date}.json           ← Coinglass 全市场聚合快照（OI/清算/交易所分布）
│   ├── cme_futures/{date}.json         ← CME BTC 期货仓位快照（全月份 OI + 期限结构）
│   ├── etf_flows/etf_flows.csv         ← BTC 现货 ETF 资金流（Farside，12 ETF + Total，M USD）
│   └── cache/{SYMBOL}_indicators.parquet ← 指标缓存（派生产物，mtime 失效）
│
├── frontend/                     ← Web 前端（Astro，ADR-0003：32 专题页已全迁 .astro，迁移完成）
│   ├── src/
│   │   ├── layouts/TopicLayout.astro ← 专题页通用骨架（head 样板/左侧树+窄屏抽屉/页头/主题等运行时行为）
│   │   └── pages/                ← 32 个专题页 .astro（liquidity 8 / rates 4 / 单页族 9 / assets 11）
│   ├── public/                   ← SPA 壳 + 共享资源（原 static/ 搬入，Astro publicDir 原样拷贝）
│   │   ├── index.html            ← 主仪表盘 SPA（vanilla，不迁）
│   │   ├── js/                   ← 共享 JS（site-nav / echarts-theme / rates-common 等，不进打包管线）
│   │   └── css/ vendor/ api/ favicon.svg _redirects
│   ├── astro.config.mjs          ← Astro 配置（output static，publicDir/outDir 用默认）
│   ├── package.json              ← npm 依赖（仅 astro）
│   └── .nvmrc                    ← Node 22 LTS
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

**每日自动（无需本地操作）**：GitHub Actions `daily-fetch` workflow 每天
北京时间 05:00 自动拉取**不依赖 IBKR/TWS** 的数据源并 commit + push，本地 `git pull` 即得：
`fred` / `cboe` / `ofr` / `srf` / `tsy` / `cfets` / `shapiro` / `sce` / `treasury` / `yfinance`（17 品种资产快照）
`barchart_futures` / `barchart_vol` / `cot` / `rate_expectations` / `fed`（Barchart 期货曲线、Barchart 波动率 30 指数快照、CFTC COT、FOMC 概率、FOMC 声明+演讲）
`financials` / `sec`（财报三张表 + SEC 10-K/10-Q 原文）
`minute_bars`（全部股票+指数 1d 日线 + 5m/15m/1h/4h 分钟线，yfinance 原始价与 IBKR 一致；1d 全量历史，5m/15m 深度 60 天、1h/4h 2 年）。

> 部分数据源失败时：成功的数据照常 commit，失败列表写进当日 commit message 的「失败:」段
> （`git log -1` 即见），job 标红，Pages 照常部署。不用再 grep Actions 日志找 `FAIL`。

> **四个 cron**：`daily-fetch` 北京 05:00（全量，含收盘后的价格类）；
> `fetch-refresh` 北京 23:15（只跑下午才发布昨日观测的源：`fetch_fred` / `fetch_cboe` /
> `fetch_fsi`，~3min）；`fast-refresh` 工作日 UTC 13-20 点每 15 分钟（盘中价量源：
> yfinance/cboe/barchart_vol/rate_expectations/polymarket，另 FRED 仅 UTC 13-14 点班次拉）；
> `crypto-refresh` 每小时全天候 7×24（加密三源：crypto_derivatives/coinglass/etf_flows，
> 从 fast-refresh 拆出）。upsert 幂等，无新数据即不 commit。
> 数据 commit 用 PAT（secret `DATA_PUSH_TOKEN`，fine-grained，仅本仓 contents:write）push，
> **但 push 本身不触发部署**：无论 GITHUB_TOKEN 还是 PAT，Actions 里的 push 都认证为
> `github-actions[bot]`（App 身份），GitHub 递归保护不为其创建 workflow run
> （实测 262 个 data commit 只触发过 1 次部署，且那次是本地手动 push）。
> 所以四个数据 workflow 在 commit 后都显式跑 `gh workflow run deploy-pages.yml --ref main`
> （job 需 `permissions: actions: write`）；无新数据 → 不 commit → 不 dispatch，空跑归零。
> PAT 到期后数据 push 会失败标红，需续期。
> 部署已切到 Cloudflare Pages 单目标（https://market-base.pages.dev ，根路径）。
> 部署链（ADR-0003，工单 #11/#12）：`export_pages`（JSON 落
> frontend/public/api/）→ `astro build`（public/ 拷贝 + .astro 编译 → frontend/dist）
> → wrangler 部署 frontend/dist，内容哈希增量上传；secrets：`CLOUDFLARE_API_TOKEN` /
> `CLOUDFLARE_ACCOUNT_ID`，token 需 Pages Edit + User→Memberships Read，
> wrangler-action v3 参数名是驼峰 `apiToken`）。GitHub Pages 已下线，
> 原站 heliar-k.github.io/market-base 停在最后一次部署作冻结备份。

> **时效断言 ≠ 退出码**：`uv run python -m src.data_freshness`（44 个数据集，预算表在文件内）
> 两个 workflow 收尾都会跑，超预算则记入 FAILED_LIST 标红。加新 fetcher 时在那张表补一行。

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
./bin/fetch_barchart_futures        # Barchart 期货期限结构（10 品种全合约，IBKR 替代源）
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
./bin/fetch_fx                     # 外汇对日线 16 对（timsun /assets/fx）
./bin/fetch_etf                    # ETF 全量清单 + 精选池（timsun /assets/etfs）
./bin/fetch_options_structure      # 22 标的期权结构快照（GEX/DEX/Vanna/Charm，~3min）
./bin/fetch_crypto_derivatives     # 加密衍生品快照（OKX + Deribit + CME）
./bin/fetch_crypto_basis             # CME BTC 基差日序列（Yahoo BTC=F proxy）
./bin/fetch_coinglass                 # Coinglass 全市场聚合（Jina Reader，OI/清算/交易所分布）
./bin/fetch_cme_futures                # CME BTC 期货仓位（官方 Settlements API）
./bin/fetch_polymarket                 # Polymarket 预测市场监测（免 key 直连）
./bin/fetch_etf_flows                 # BTC 现货 ETF 资金流（Farside via Jina Reader）
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
# 静态部署（Cloudflare Pages，公开仓库）：deploy-pages workflow 部署链 =
#   uv run python -m src.export_pages（API JSON → frontend/public/api/，工单 #12 起唯一模式）
#   → npm run build（astro，frontend/dist）→ wrangler deploy → https://market-base.pages.dev/
#   触发：前端/后端 push 自动触发；数据更新由数据 workflow commit 后显式 dispatch
#   （Actions 里的 push 不产生 workflow run，见「四个 cron」段说明）；
#   手动：gh workflow run deploy-pages.yml --ref <分支>
#   等部署结果别用 `gh run watch`：非 TTY 下它只打一次状态快照就 exit 0（假成功，还会把上一轮
#   部署的 wrangler 输出混进来）。轮询 `gh run view <id> --json status --jq .status` 直到 completed，
#   再 curl 线上产物（如 /css/app.css）grep 改动关键字才算真生效
# 限制：K 线仅近 3 年、相关性页仅近 5 年、诊断面板无光标回看（静态预渲染的固有降级）
# 利率专题（timsun.net/rates 复刻）：/rates/ 入口页 → 联邦基金/收益率曲线/利率定价（拍卖已并入 /treasury/）
# 研判由 src/rates_analysis.py 规则引擎生成，LLM 接入点：generate_analysis() → _llm_generate()

# 测试
uv run python -m pytest                            # 全量测试（707 个）

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
- ⚠️ 调用 `ensure_yf_proxy()` 的入口在 Actions 必须设 `YF_NO_PROXY=1`（无代理环境直连）；
  env 是 **step 级不继承**，漏配即静默 ConnectionError（2026-09 options_structure 因此停更 12 天）

### 6. IBKR 端口与限制
- 端口：`4002` = 模拟账户（默认，行情订阅数有限 ~3-5，GEX 用 `--batch-size 3` 小批量串行）；`4001` = 实盘只读（`connect_ib` 自动带 `readonly=True`，可 `--batch-size 50`）
- `ibkr_fetcher.py` 连接时依次尝试 4002 → 4001（4001 按只读连接）
- Greeks 当日快照：`data/gex/{SYMBOL}_greeks_YYYYMMDD.csv`，`--reuse-greeks` 复用后只拉 yfinance OI；gamma 贴近墙位对 spot 敏感，spot 动 1%+ 要重拉
- gamma 符号惯例：call 正 / put 负（IBKR modelGreeks 恒非负，`fetch_options_greeks` 里统一翻转，与 `greeks_from_yf` 一致）
- 每次请求后 `sleep(15s)` 避免限流（配置在 `config.ibkr.request_delay_seconds`）

### 7. 反爬抓取工具箱（新 fetcher 优先复用）

遇到 `202/403 + 空 body/HTML challenge` 时，按站点反爬类型选工具，**不要重造轮子**：

| 工具 | 适用场景 | 参考实现 |
|------|---------|---------|
| `jina_reader.jina_fetch(url)` | Cloudflare 拦截 / JS 渲染页（页面→Markdown） | `coinglass_fetcher` / `etf_flows_fetcher`（Farside） |
| `barchart_client.core_get(params, referer, auth)` | Barchart core-api（期货曲线/波动率快照/远期点/期权链）；直连失败**自动降级** playwright 无头浏览器（进程单例） | `barchart_futures_fetcher` / `barchart_vol_fetcher` / `cfets_fetcher` |
| `curl_cffi requests.Session(impersonate="chrome")` | TLS 指纹检测的直连 JSON API | `news_fetcher`（Yahoo NCP） |

- Jina 免费额度 ~20 RPM，日频 cron 量够；慢加载页加 `x-timeout` / `x-no-cache` / `x-wait-for-time` 头
- AWS WAF（JS challenge + aws-waf-token）只有真浏览器能过；Jina 能过 WAF 但拿不到 cookie，所以 Barchart 用 playwright 页内 fetch 而非 Jina
- 测试环境：`tests/conftest.py` autouse 禁用浏览器通道（真起 chromium 会污染 TUI 测试的 event loop）
- Actions 已预装 chromium（daily-fetch workflow 的 "Install playwright chromium" step）

---

## 编码约定

- **语言**: 注释和文档用中文（面向中文用户），代码标识符用英文
- **格式化**: ruff (select E/F/I/W) + ruff-format，`pre-commit` 在 git commit 时自动执行（`ruff --fix` + `ruff-format` 自动修并重新暂存）。**写完代码无需手动跑 ruff/pre-commit**，只验证功能正确性（代码能跑）即可；E501（行太长）不会被自动修，commit 被拦时再手动改
- **类型提示**: 所有函数签名带类型注解，用 `|` 替代 `Optional`（Python 3.10+）
- **import**: 先标准库 → 第三方 → `src.*`（`isort` 自动处理）
- **测试**: pytest 测试套件（`tests/`，707 个测试），用 `tmp_path` 隔离 + autouse fixture 清理缓存。运行 `uv run python -m pytest`
- **分析层约定**: 专题分析模块（`*_analysis.py`）只读 CSV 不写盘，读 CSV 统一走 `src/analysis_utils.py` 的 `read_csv_or_empty`，不各写各的 `_read`

### 8. 主站 Web UI/UX 设计原则（新面板/重构对齐用）

主站 = `frontend/public/index.html` SPA（仪表盘/技术/宏观/关联四视图）+ 32 个 .astro 专题页
（`frontend/src/pages/`，TopicLayout 骨架，timsun 复刻）。新面板与重构遵守：

- **主题只走 CSS 变量**：颜色一律用 `:root` / `body.dark` 定义的 `var(--bg/--surface/--border/--text/--accent/…)`（见 `frontend/public/css/app.css` 顶部），禁止硬编码背景/文字色；亮暗双主题都要可用。ECharts 图统一 `echarts-theme.js` 的 `macro`/`macroDark` 主题 + `reThemeECharts` 响应 `theme-changed` 事件
- **图例色标单源**：`echarts-theme.js` 的 `RE_LEGEND`（实线/虚线/点线/点划线/阴影带/带圆点实线，path 自绘）+ `reSyncLegend(option)`（按 series 线型推形状、把图例色块对齐到线色——ECharts 图例只读 `series.color`，
  不读 `lineStyle.color`）。`R.lineOption` 已内置；不走 lineOption 的图包一层 `setOption(reSyncLegend({...}))`。
  页面**禁止**手写 `legend.data[i].icon` 或 `legend.formatter` 标注线型；尺寸走主题默认 18×6（散点图例自行给正方形尺寸）。
  回归测试：`tests/test_legend_sync.py`
- **小节标题单源 `Section` 组件 + 术语中文为主**：专题页每个小节用 `components/Section.astro`（`.re-sec-title` 带 accent 竖条），
  **不写裸 `<div class="re-section"><h2>`** —— 静态内容自己带卡面（`.vol-signal-card` / `.score-card` 等已有 border+bg）时，
  外层再套 `.re-section` 就是卡中卡双层框，标题形式也与全站不一致。`.re-section` 只留给 JS 动态生成的卡片集
  （如七段叙事、地缘事件、压力测试——卡片本身就是要重复的条目，不进静态层）。守卫只查 HTML 结构层
  （首个 `<script is:inline>` 之前）：`tests/test_static_single_source.py::test_static_sections_use_section_component`。
  标题行尾部要放状态胶囊用 `slot="head"`（如 VIX 期限结构的升贴水徽章），不另造平行头部结构。
  **文案中文为主**，国际通用术语（VIX / SKEW / Contango / Term Structure）保留英文：
  弱化说明走 `sub` prop（`.re-sec-sub`），需与数据联动的状态文字在 island 里用映射表（`TERM_STATE` 一类），
  标题本身不写成纯英文
- **复用既有类，不造平行组件**：卡片 `.chart-card`、控件条 `.controls`/`.range-controls`/`.range-btn`、数据行 `.diag-row`、语义色 `.up/.down/.neutral`、统计卡 `.dash-stat`/`.dash-card`；只给面板私有结构加 `面板名-` 前缀的新类
- **KPI 卡行单源 `R.cards()` + `.re-cards`**（`rates-common.js` / `special.css`）：卡行是 flex，
  卡宽由 CSS 一处定（`flex` 增长到 **275px = 全站 4 列档** 封顶），页面**不写**内联
  `grid-template-columns` / `font-size`。为什么是 flex 不是 grid：grid 的 `auto-fit` 列数按轨道**上限**算，
  `minmax(130px, 275px)` 在 1440 下只给 3 列（6 卡行被压成两排）；flex 的「先平分增长、到 275 停」
  才是想要的形状——≥4 卡铺满整行，2-3 卡行左对齐留白（不再 562px 拉满，同 `/liquidity/reserves/`）。
  四个旋钮：默认档（数字卡）/ `.re-cards-wide`（长文本、百分位条、内嵌框的卡，basis 250）/
  `.re-card-wrap`（value+sub 换行不截断）/ `.re-card-dense`（密集卡：紧 padding + 17px 值）。
  `R.cards(items, rowCls)` 的 item：`{label, value, sub, accent, cls, vcolor}`——`value`/`sub` 是 HTML
  （涨跌 chip 走 `R.chgSpan`，着色走 `vcolor: 'var(--up)'` 或 `<span class="up">`），`cls` 给单卡加类。
  卡内还要放图/表/条的复合卡（期权墙、NOW 百分位卡）才允许手写 `.re-card`，但仍在 `.re-cards` 行里。
  守卫：`tests/test_static_single_source.py::test_card_rows_use_single_source_grid`
- **「先看结构、再看数据」双层节奏**：面板第一屏给结论层（状态条/评分/报警/规则引擎叙事，一眼可读），下层给可交互的数据层（图/表/热力图），两层通过点击联动（点结论→定位数据，点数据→弹出明细）
- **微观数据须有宏观锚点**：展示单资产/单指标时，附带其在全局中的坐标（分组、分位、相对强弱），避免孤立数字
- **工具栏模式统一**：预设/模式切换按钮置顶（`.range-btn` 风格），日期范围按钮居右（`macro-common.js` 的 `MACRO_DATE_RANGES`），状态写进底部状态栏（`updateStatus()` 约定）
- **分析层 vs 展示层**：叙事文本由 Python 规则引擎（`*_analysis.py`）或前端常量表生成，前端不做复杂计算；图表数据走 `/api/*` 端点，静态部署走 `src/export_pages.py` 预渲染（新端点必须注册）
- **README 式空状态**：数据缺失时给出原因与修复命令（如「IBKR 未启动，运行 ./bin/fetch_xxx」），不白屏不静默
- **数据时效提示统一格式**：页面页头 `re-as-of` 统一为「**数据截至** [源] YYYY-MM-DD[ · 源 date]」——
  前缀固定「数据截至」，多源/双时点用 ` · ` 分段（如 treasury「TIC 2026-06-01 · Bill 占比/拍卖 2026-09-04」、
  inflation「月频 2026-07-01–2026-07-01 · 盈亏平衡 2026-09-04」）；月频区间写「月频 {起}–{止}」；
  日期一律 ISO。数据缺失显示空，不要自造前缀（「数据日期:」「快照时间 ·」等已废弃）。
  **文案只能由 `rates-common.js` 的 `R.asOf(...)` / `R.asMonth(...)` 组装**，页面不写模板串
- **页脚两行单源**：`TopicLayout` 的 `<footer class="re-foot">` 只有两行 —— ① 本页数据源/口径
  ② 免责 + 返回首页。全站级的来源枚举与「每日自动拉取」说明已删（前者与各页「数据源」重复，
  后者不是用户要看的信息），品牌已在左侧栏顶/浏览器 title 里，不在页脚重复。
  术语只用**「数据源」**（「数据来源」已废弃，守卫会红）。第 ① 行走 `<slot name="foot">`：各页只交
  一个空容器 `<div class="re-foot-page" slot="foot" id="re-foot"></div>`，文案**只走
  `rates-common.js` 的 `R.foot(gen, { src, note })` 组装**（页面不写模板串、不重复 id）；
  有研判段就传 analysis 的 `generator`（出「研判生成：规则引擎（LLM 预留）」），纯数据页传 `null`；
  **时间不写页脚**——页头 `#re-as-of` 已给，同页不重复两次。空行不占位（`.re-foot-page:empty`）。
  侧栏底部那句（「数据每个交易日自动更新 · 不构成投资建议」）为导航区弱化注记，与页脚并存。
  守卫：`test_pages_own_one_foot_slot` / `test_foot_text_only_via_r_foot` /
  `test_page_level_source_term_is_data_source`
- **head / 导航单源（Astro，ADR-0003）**：专题页 `<head>` 样板与**左侧专题树**由
  `frontend/src/layouts/TopicLayout.astro` 构建期统一渲染（页私有 head 追加走 `slot="head"`）；
  左侧树 + SPA 侧栏专题树唯一数据源 = `frontend/public/js/site-nav.js`（ESM：`export const SITE_NAV`，
  TopicLayout 构建期与 macro-view.js / dashboard.js 运行时 import 同一份）。
  专题页左侧树**复用 SPA 那套 `.macro-nav*` 类**（样式单源在 `app.css`，不造平行组件），
  `body.re-has-side` 只在 ≥1025px 让位 240px，窄屏与内嵌（`body.embedded`）不让位；
  ≤1024 树收进抽屉，由 TopicLayout 渲染的 `#macro-nav-toggle`（☰）+ `.macro-nav-backdrop` 唤出
  （样式与行为与 SPA `macro-view.js` 同一套：toggle / backdrop / Esc）。
  **顶栏胶囊 Tab（`.re-nav`）已整条删除**（专题页任何宽度都不再有两套导航；它在窄屏会撑成
  2-4 排胶囊压住 `.page-toc`，且 11 个胶囊无当前项样式，根上就没法当层级导航）；
  当前页高亮 / 当前组展开由构建期算（SITE_NAV 的 `.html` 要先归一化成目录形态才能对上产物 URL）。
  新专题：写 .astro 页（套 TopicLayout）→ 在 `SITE_NAV` 登记即可（无别处白名单）
- **语义色两档制（图形档 / 文字档）**：`frontend/public/css/tokens.css` 里
  `--color-up/down/neutral/warn/warn-light/warn-deep/brand/hawk/dove` = **图形档**（只准用于
  填充 / 边框 / 线色），`--color-*-text`（每色逐一对应；`tokens.css` 另给四个历史短名别名
  `--up/--down/--flat/--warning` 直指文字档，`app.css` 的 `--accent-ink` = brand 的文字档），
  `color:` 只能用它。亮色下图形档当文字色只有 2.1–3.6:1（暗色达标），所以这个 bug 长期不暴露。
  `--accent-ink` 亮 #155ecb 5.69:1（页面底）、暗 #a5d3ff（卡面 10.99）—— 暗色也不别名回
  图形档：#58a6ff 对比虽过 AA，但满饱和纯蓝在暗底小字号下发颤，所以 brand 两档分开。ECharts 的 `label`/`axisLabel` 文字色走 `R.colors()` 的
  `greenText/redText/orangeText`。回归测试：`tests/test_semantic_text_contrast.py`
  （除对比度与三条 `*_color` 守卫外，还锁一条：DOM 内联 `style="…"` 里不得出现
  `reCssVar()` 的解析结果）
- **内联色只给引用，不给解析结果（「用哪个档」之外还有「什么时候解析」）**：写进 DOM 的
  `style="…"` 一律用 `var(--token)`；透明底/边框用
  `color-mix(in srgb, var(--token) N%, transparent)`（hex 后缀换算：`66`→40%、`1a`→10%、
  `18`→9%）。`reCssVar()` 把变量解析成字面 hex，字符串一进 DOM 就固化，用户切亮/暗主题
  不刷新页面就还是旧主题色 —— **它只该出现在 canvas 里**（ECharts `itemStyle`/`axisLabel`/`rich`、
  TradingView 线色），canvas 不认 `var()`/`color-mix`。同一个取色表两边都要用时，存 token 名
  单源 + 一个渲染期解析函数给 canvas（见 `cross-correlation.js` 的 `GROUP_TOKENS` / `GROUP_COLORS`）。
- **多端点页必须段级容错**：主端点用 `R.get`，其余一律 `R.getOpt`（任何失败返回 `null` 不抛）。
  根因：静态导出端 `src/export_pages.py` 的 `_safe()` 会跳过当日缺数据的端点，
  `frontend/public/api/…`（Astro directory 路由，**无 `.json` 后缀**：`/api/credit/stress` 而非
  `/api/credit/stress.json`）**可以合法 404** —— 用 `R.get` 进 `Promise.all` 会让一个源挂 =
  整页空白。缺源段自己渲染空态（`R.fail`），其余段照常。`R.fail(ids, e)` 里的 id 必须在同页有
  `id="…"`（否则静默什么都不做）。后端字段同理：名字像第三方原文的（`i.name` 等）先回查真实来源，
  别信字段名
- **数字 / 涨跌 / 转义单源**：格式化数字走 `R.num`，涨跌 chip 走 `R.chgSpan`，HTML 转义只走
  `R.esc`（三者都在 `rates-common.js`），页面不造平行 helper。`R.table(headers, rows, formatters,
  keys, html)` 第 5 参 `html=true` 时**默认**格式化器会转义，但**显式** formatter 返回的 markup
  不转义（它们要着色 `<span>`，自己负责转义）
- **无图页声明 `chart={false}`**：`TopicLayout` 的 `chart` prop 控制是否加载 `echarts.min.js`
  （1 MB 同步阻塞）；卡片/表格页必须传 `chart={false}`
- **前端约定守卫测试**：`tests/test_static_single_source.py`（≥2 端点页必用 `R.getOpt`、
  `R.fail` 目标 id 存在、转义只走 `R.esc`、「数据截至」只走 `R.asOf`、每个字面量
  `'/….html'` 链接都要有 `_redirects` 重写行（#26，漏一行即静默 404；刻意不查孤儿），
  另含 `node --check` 过全部 `is:inline` 脚本）。改前端收尾必跑：`uv run python -m pytest tests/test_static_single_source.py
  tests/test_semantic_text_contrast.py -q` + `cd frontend && npm run build`
- **已审计过的死路（勿重开）**：全站零自实现手势，不加 `touch-action`（加了反而禁掉表格纵向滑动）；
  触控目标门槛是 **24 CSS px**（WCAG 2.2），44 是 iOS pt / Android dp，`.range-btn` 实算 ≈24.4px 已过线

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

## Agent skills

### Issue tracker

Issues 与 specs 走 GitHub Issues（gh CLI 读写）。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个默认 triage 标签（needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix）。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根级 CONTEXT.md + docs/adr/。见 `docs/agents/domain.md`。

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
