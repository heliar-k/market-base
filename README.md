# ticker-toolkit

金融数据管道 + 技术分析工具箱。每日自动拉取美股/指数/宏观/期权/期货数据，支持 K 线技术指标计算、GEX 分析、交互式 TUI 报告。

> **uv 项目** — 所有 Python 命令用 `uv run` 前缀。数据拉取脚本在 `bin/` 下（内部已用 `uv run`），直接执行即可。

---

## 快速开始

### 1. 安装

```bash
uv sync
```

### 2. 配置密钥

在 `.env` 中设置（不提交 git）：

```
FRED_API_KEY=你的密钥          # https://fred.stlouisfed.org/docs/api/api_key.html
HTTPS_PROXY=socks5://127.0.0.1:7890   # yfinance 需代理
```

### 3. 拉取数据

```bash
./bin/fetch_ibkr          # 股票 + 指数日线（需 TWS/IB Gateway 跑在 4002）
./bin/fetch_fred          # 46 个 FRED 宏观系列
./bin/fetch_cboe          # CBOE 波动率（OVX、VIX 期限结构）
./bin/fetch_yfinance      # yfinance 资产价格（需代理）
./bin/fetch_commodities   # 期货日线（整条曲线）
./bin/fetch_options       # 期权链参数
```

### 4. 启动 TUI

```bash
uv run python -m src.tui.app
```

---

## TUI 用法

双模式，`Tab` 切换：

### 技术分析模式（默认）

选标的（股票/指数）→ K 线图 + 副图 + 诊断侧栏。

| 键 | 作用 |
|----|------|
| `↑` `↓` | 侧栏选择标的 |
| `←` `→` | 回看：移动光标到历史 K 线，侧栏显示当天诊断 |
| `1` `2` | 副图 1/2 轮换指标（RSI/MACD/Stoch/CCI/MFI） |
| `b` `m` `s` | 切换布林带 / MA120 / SuperTrend 叠加 |
| `Tab` | 切到宏观模式 |
| `q` | 退出 |

**K 线图三层**：主图（candlestick + MA5/10/20/60 + 布林带 + vline 光标）+ 2 副图（默认 RSI + MACD）+ 诊断侧栏（综合评分、MA 方向、RSI/MACD 状态、ADX 趋势、形态命中、关键价位）。

### 宏观模式

选 FRED 分类 → 系列 → 时序折线 + 期限结构。

| 键 | 作用 |
|----|------|
| `↑` `↓` `→` | Tree 展开分类 / 选系列 |
| `空格` | toggle 当前系列到叠加集合（多系列对比） |
| `←` `→` | 期限结构快照日期移动（仅 rates/tips） |
| `Tab` | 切回技术分析模式 |
| `q` | 退出 |

**派生指标**（2s10s 利差、净流动性、BEI 5y/10y、SOFR-IORB 利差）可作为系列叠加。BEI 横跨 rates+tips，选中后会自动合并两个分类数据。

---

## CLI 用法

### 技术分析（JSON 输出）

```bash
uv run python -m src.analyze data/stocks/AAPL.csv              # 输出 JSON
uv run python -m src.analyze data/stocks/AAPL.csv --as-of 2024-06-01   # 回看到指定日期
```

Rich 报告渲染已迁移至 TUI，CLI 仅保留 JSON 出口（供 cron / 管道 / 外部工具消费）。

### GEX 计算

```bash
uv run python src/compute_gex.py                  # AAPL（默认）
uv run python src/compute_gex.py --symbol TSLA    # 指定品种
uv run python src/compute_gex.py --expirations 6  # 6 个到期月
```

---

## 测试

```bash
uv run pytest                # 全部（124 个测试）
uv run pytest -v             # 详细
uv run pytest tests/test_tui_tech_mode.py   # 单个文件
uv run ruff check src/ tests/               # lint
```

测试用 `tmp_path` 隔离 + autouse fixture 清理缓存，不污染工作区。

---

## 项目结构

```
├── src/
│   ├── analyze.py        # 技术分析诊断引擎（analyze + detect_cdl_hits）
│   ├── indicators.py     # 技术指标计算（MA/RSI/MACD/BB/ADX/Stoch/SuperTrend 等）
│   ├── macro.py          # 宏观派生指标（2s10s/净流动性/BEI/SOFR-IORB）
│   ├── cache.py          # 指标缓存层（parquet + mtime 失效）
│   ├── config.py         # 统一配置（FRED/IBKR/yfinance/期货 + TERM_SERIES）
│   ├── compute_gex.py    # GEX 计算
│   ├── fetchers/         # 数据拉取（IBKR/FRED/CBOE/yfinance/期货/期权）
│   └── tui/
│       ├── app.py        # KlineApp 主入口 + 键盘绑定
│       ├── state.py      # 纯逻辑状态机（TuiState/TechView/MacroView/LookbackCursor）
│       ├── screens.py    # 主屏三栏布局 + Worker 化加载 + 防抖
│       └── widgets/
│           ├── kline_chart.py   # K 线图（candlestick + 副图 + vline 回看）
│           ├── diag_sidebar.py  # 诊断侧栏
│           └── macro_chart.py   # 时序折线 + 期限结构
├── bin/                  # 可执行数据拉取入口
├── data/                 # 增量 CSV / parquet 缓存
├── tests/                # pytest 测试
├── docs/adr/             # 架构决策记录
├── CONTEXT.md            # 领域术语表
└── AGENTS.md             # 项目指南（详细）
```

---

## 架构决策

- **ADR-0001**：TUI 回看用键盘 ←→ + plotext `vline`（不用鼠标悬停反推坐标）
- **ADR-0002**：源码包从 `code/` 重命名为 `src/`（避免与 stdlib `code` 模块冲突）

详见 `docs/adr/`。

---

## cron 自动拉取

```bash
# 每个交易日美股收盘后（北京时间 05:00）
0 5 * * 1-5 cd /path/to/ticker-toolkit && bash src/run_fetch.sh >> logs/cron.log 2>&1
```
