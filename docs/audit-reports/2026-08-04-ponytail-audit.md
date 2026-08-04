# ponytail-audit — 全仓库过度设计审计（2026-08-04）

范围：`src/`（fetchers / TUI / 分析引擎）+ `static/`（JS 前端）+ `bin/` + `tests/`，共 ~21.4k 行。
方法：通读 + rg 交叉验证调用方，只查过度设计（dead code / 重复实现 / 单实现抽象 / 投机灵活性），不涉及正确性与性能。

**净收益：约 -950 行，0 个依赖可减。**

---

## 按削减量排序

### 1. delete — `static/js/stock-kline.js`（-412 行）
全仓无任何引用：index.html 只加载 `/js/app.js`，app.js 从不 import 它。是 tech-view.js 出现前的重复件（同样的 K 线+均线+BB+RSI+MACD+量能应用）。
`[static/js/stock-kline.js]`

### 2. shrink — ibkr_fetcher 1d 路径与 _symbol_fetch.run 重复（-100 行）
`ibkr_fetcher.main()` 的 `1d` 分支把 `_symbol_fetch.run()` 的编排完整重写了一遍（connect → dry-run → 逐品种循环 → 增量过滤 → yfinance 回退 → upsert）。`bin/fetch_ibkr`（默认 1d）和 `bin/fetch_stock`/`fetch_index` 互不知情地拉同一份数据。
做法：`1d` 分支委托给 `_symbol_fetch.run()`，只保留 bar-size / `1w` / `--yf-only`（Actions 在用）分支。
`[src/fetchers/ibkr_fetcher.py, src/fetchers/_symbol_fetch.py]`

### 3. shrink — macro.py 派生指标规格三份并存（-80 行）
同一份知识存在三处：10 个近同构的 `_spread_*`/`_bei_*` 函数（每个都是列子集检查+公式）、`DERIVED_INPUTS` 字典、`derive_macro` 里的 derivations 元组，靠注释约定"保持同步"。
做法：收敛为一个 `{name: (input_cols, fn)}` 规格，~80 行 → ~20 行。
`[src/macro.py]`

### 4. shrink — credit / volatility 分析模块共享工具复制（-50 行）
`_latest`、`_chg_prev`（credit 的 docstring 自己承认"与 volatility_analysis 同构"）、`_chg_pct`、`_pct`/`_percentile`、`_zone` 在两个模块里逐字节重复；另外 4 个分析模块各有一份 `if not path.exists(): return empty; pd.read_csv(...)` 读取助手（共 6 份）。
做法：抽 `analysis_utils.py` 共享。
`[src/credit_analysis.py, src/volatility_analysis.py, src/rates_analysis.py, src/fed_analysis.py]`

### 5. shrink — 前端主题/缩放样板 ×4（-45 行）
`onThemeChanged`（dispose→reThemeECharts→新 ResizeObserver）和 `observe()` 助手在 dashboard / macro-view / liquidity-heatmap / cross-correlation 四个视图各写一遍（~15 行 × 4）。
做法：echarts-theme.js 里导出一个 `makeChart(dom, opts)`。
`[static/js/{dashboard,macro-view,liquidity-heatmap,cross-correlation,echarts-theme}.js]`

### 6. delete — LLM 预留脚手架 ×4 模块（-24 行）
`_llm_generate()` + `_LLM_ENABLED = False` + `_llm_try()` 在 credit/rates/fed/volatility 四个模块重复，且 `_LLM_ENABLED=False` 意味着分支永远不会走。纯死接缝。LLM 真正接入时再加。
`[src/{credit_analysis,rates_analysis,fed_analysis,volatility_analysis}.py]`

### 7. shrink — rates_analysis 期限列复制 config（-17 行）
`DGS_COLS` + `TIPS_COLS` 与 `config.TERM_SERIES["rates"/"tips"]` 完全一致（同名 16 个、同序；config 注释自己写了"勿另起映射"）。直接 import。
`[src/rates_analysis.py]`

### 8. shrink — 利率走廊卡片双实现（-60 行）
macro-view.js 的 `buildCorridorTable` + 目标区间/下次会议卡片 + `/api/fomc/calendar` 拉取，重复了 static/rates/expectations/index.html 的内联脚本；该页面还没加载 rates-common.js，自己重写了 `isDark()`/`echartsTheme()`。保留一份。
`[static/js/macro-view.js, static/rates/expectations/index.html]`

### 9. shrink — TUI 两图表组件重复（-35 行）
`_clean()` 和 `_SubPlot` 在 kline_chart.py 与 macro_chart.py 原样重复。抽共享助手。
`[src/tui/widgets/{kline_chart,macro_chart}.py]`

### 10. delete — TUI 死代码（-27 行）
`LookbackCursor.at_start()/at_end()/reset_to_end()`、`SubplotSlots.current()`（rg 证实零调用）、`MainScreen._macro_category_loaded`（写 3 次从未读）、`KlineChart._window`（只写不读）。
`[src/tui/state.py, src/tui/screens.py, src/tui/widgets/kline_chart.py]`

### 11. shrink — server.py `_sanitize` + months 字典（-40 行）
`_sanitize` 包在 28 个 return 上，重造 FastAPI 自动应用的 `jsonable_encoder`（唯一增量是 NaN→None）。一个 `default_response_class` 的 JSONResponse 子类即可替换全部 28 处包装。另：10 项的 `months` 字典在 `get_liquidity_overview` 与 `get_liquidity_compare_spx` 里整段复制。
`[src/server.py]`

### 12. delete/shrink — analyze.py（-19 行）
`--json` flag 的 help 自己写着"无实际分支作用"——删。`x = float(x) if pd.notna(x) else None` 惯用法重复 ~20 次——`_num()` 助手省 ~15 行。
`[src/analyze.py]`

### 13. delete — 零星死代码（-12 行）
`macro-view.js disposeAllCharts()`（从未调用）、`charts-common.js initTooltip()`（无人 import）、`tests/test_smoke.py` 的 `assert 1+1 == 2` 占位。
`[static/js/macro-view.js, static/js/charts-common.js, tests/test_smoke.py]`

### 14. delete — DataPoint 只写字段（-6 行）
`as_of` / `source` / `formula` 全仓无人读（as_of 仅 yfinance 写、只进日志）。保留 metric/value/volume/qa_status。
`[src/fetchers/quality.py]`

### 15. yagni — 默认值永不被覆盖的旋钮（-32 行）
`seed_history_if_short(min_rows=)`、`save_daily_csv(date_col=)`、`core_get(timeout=)`、`fetch_single(duration_override=)`、`MacroChart.update_data(overlaid=)`；`stock_fetcher.py`/`index_fetcher.py` 是只差 2 个字符串的 29 行包装（可合并或折进 `_symbol_fetch.__main__`）；`commodities_fetcher._fetch_bars` 重写 `bars_to_dataframe`；`save_chain_csv` 重读自己刚写的文件数行数；`TERM_SERIES` 的键完全可由 `TERM_INFO` 派生。
`[src/fetchers/*, src/tui/widgets/macro_chart.py]`

---

## 已核查为精简（无需动）

- fetchers：fred / cboe / fsi / srf / tsy / cot / fed / treasury / bgcr / cgb / shapiro / sce / cfets / barchart_futures / barchart_options —— 每个单一数据源、错误处理诚实（tsy/cfets/cot/srf/shapiro/sce 输出无仓内读者属产品性质，非浪费）
- TUI 核心：state.py 状态机（Mode/TuiState/TechView）、kline_chart 渲染、diag_sidebar
- 前端：nav.js、macro-common.js、rates-common.js
- 期权定价：pricing.py 是 compute_gex/sell_put/hedge_planner 三处的正当收敛点

## 顺手发现（超范围，另走常规 review）

- `indicators.add_smc_liquidity_sweep` 双循环 O(n²)
- `_sanitize` 不处理 `pd.NA`（遇 pd.NA 会漏过）

## 结论

全部是删除/收敛型改动，零新增依赖。最大的单刀是删除死文件 stock-kline.js（412 行）；其余是合并：一条 IBKR 拉取路径、一份宏观派生规格、一份共享工具模块。本报告只列发现，未应用任何修改。
