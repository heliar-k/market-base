# TUI → HTML 重构方案

> 2026-07-05 | 目标：用 Web 界面替代 Textual TUI，分析引擎不动。

---

## 架构总览

```
                    ┌──────────────────────────┐
                    │   🧠 分析引擎（不动）       │
                    │                           │
                    │  src/analyze.py           │
                    │  src/cache.py             │
                    │  src/macro.py             │
                    │  src/indicators.py        │
                    │  src/config.py            │
                    └──────────┬───────────────┘
                               │ import / 直接调用
                    ┌──────────▼───────────────┐
                    │   🌐 API 层（新增）        │
                    │                           │
                    │  src/server.py            │
                    │  FastAPI + uvicorn        │
                    │  REST: /api/tech/*        │
                    │        /api/macro/*       │
                    └──────────┬───────────────┘
                               │ HTTP JSON
                    ┌──────────▼───────────────┐
                    │   🖥️ 前端（新增）          │
                    │                           │
                    │  web/index.html           │
                    │  lightweight-charts v5    │
                    │  纯 HTML/CSS/JS           │
                    │  零框架依赖               │
                    └──────────────────────────┘
```

## 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 后端 | FastAPI + uvicorn | 轻量，`analyze()` 返回 dict 天然就是 JSON |
| K 线图 | [lightweight-charts](https://github.com/tradingview/lightweight-charts) v5 | TradingView 开源，原生 candlestick，API 干净 |
| 宏观折线 | lightweight-charts LineSeries | 同一库搞定，不用切 ECharts |
| 前端框架 | 无 | 单页面，DOM 操作量小，vanilla JS 够用 |
| 样式 | 手写 CSS | clean white professional，完全可控 |
| 构建 | 无 | HTML 直接引用 CDN 的 lightweight-charts |

## 文件结构

```
web/
├── index.html          # 单文件应用（HTML + CSS + JS 合体）
├── style.css           # （可选拆分）主题样式
└── app.js              # （可选拆分）业务逻辑

src/
├── server.py           # ★ 新增：FastAPI 路由
├── analyze.py          # （不动）
├── cache.py            # （不动）
├── macro.py            # （不动）
├── indicators.py       # （不动）
└── config.py           # （不动）

src/tui/                # 保留不删，可随时切回去
```

## API 设计

### 技术分析

```
GET  /api/symbols                              → [{name, type, exchange, currency}, ...]
GET  /api/tech/{symbol}                        → {symbol, df_json, diagnosis}
GET  /api/tech/{symbol}?as_of=2024-06-01       → 回看模式（cursor）
GET  /api/tech/{symbol}/indicators             → {RSI, MACD, Stoch, CCI, MFI, ...}
```

`GET /api/tech/{symbol}` 返回：
```json
{
  "symbol": "AAPL",
  "rows": [
    {"date": "2024-01-02", "open": 185.0, "high": 186.5, "low": 184.0, "close": 186.0},
    ...
  ],
  "indicators": {
    "MA5":   [null, null, null, null, 185.2, ...],
    "MA10":  [...],
    "MA20":  [...],
    "MA60":  [...],
    "MA120": [...],
    "BB_upper": [...], "BB_mid": [...], "BB_lower": [...],
    "SUPERT": [...],
    "RSI": [...], "MACD": [...], "MACD_signal": [...], "MACD_hist": [...],
    "STOCH_k": [...], "STOCH_d": [...],
    "CCI": [...], "MFI": [...]
  },
  "diagnosis": {           // analyze() 返回的 dict
    "total_score": 5,
    "scores": [...],
    "ma_signals": {...},
    "RSI": 65.2, "RSI_detail": "中性",
    "MACD_status": "golden_cross",
    "ADX": 28.5, "ADX_trend": "bullish",
    "cdl_bullish": ["hammer"], "cdl_bearish": [],
    "support_90d": 145.0, "resistance_90d": 155.0
  }
}
```

### 宏观分析

```
GET  /api/macro/categories                     → [{category, series: [name, ...]}, ...]
GET  /api/macro/{category}                     → {category, rows: [{date, ...}], columns: [...]}
GET  /api/macro/{category}/term-structure      → 期限结构快照（仅 rates/tips）
```

## 前端三栏布局

```
┌────────────────┬──────────────────────────────┬──────────────────┐
│  📊 Symbols    │                              │  📋 Diagnosis    │
│                │                              │                  │
│  AAPL   ●      │    ┌──────────────────────┐  │  综合评分: +5    │
│  MSFT          │    │                      │  │  ██████░░░░░░   │
│  NVDA          │    │    K 线主图           │  │                  │
│  GOOG          │    │    candlestick +      │  │  ▲ MA多头排列    │
│  ...           │    │    MA/BB/SuperTrend   │  │  ▼ RSI超买      │
│                │    │                      │  │                  │
│  ─────────     │    └──────────────────────┘  │  RSI: 65.2 中性  │
│  🌐 Macro      │    ┌──────────────────────┐  │  MACD: 金叉 ↑    │
│  rates ▶       │    │  RSI 副图            │  │  ADX: 28.5 ↑    │
│  tips ▶        │    │                      │  │                  │
│  inflation ▶   │    └──────────────────────┘  │  支撑: 145.0     │
│                │    ┌──────────────────────┐  │  阻力: 155.0     │
│                │    │  MACD 副图           │  │                  │
│                │    │                      │  │                  │
│                │    └──────────────────────┘  │                  │
├────────────────┴──────────────────────────────┴──────────────────┤
│  [时间范围滑块]  [叠加开关: MA/BB/ST]  [副图切换: RSI↔MACD↔...]   │
└──────────────────────────────────────────────────────────────────┘
```

## 交互对标

| TUI 操作 | Web 操作 |
|----------|----------|
| `↑↓` + `Enter` 选标的 | 点击侧栏标的 |
| `←→` 回看 K 线 | 鼠标 hover / drag crosshair |
| `1/2` 切副图 | 点击副图选择器下拉 |
| `b/m/s` 切叠加 | toggle 开关按钮 |
| `Tab` 切模式 | 点击顶栏 TECH / MACRO tab |
| `空格` 叠加宏观系列 | 点击系列名 toggle |
| plotext 固定窗口 | 鼠标滚轮缩放 + 拖拽平移 |

## 分阶段实施

### Phase 1 — 后端 API（src/server.py）
- [ ] FastAPI app + CORS
- [ ] `GET /api/symbols` — 返回 IBKR_SYMBOLS
- [ ] `GET /api/tech/{symbol}?as_of=` — load_or_compute + analyze
- [ ] `GET /api/macro/categories` — 返回 FRED 分类树
- [ ] `GET /api/macro/{category}` — 读 CSV + derive_macro
- [ ] `uvicorn src.server:app --reload`

### Phase 2 — 前端框架（web/index.html）
- [ ] 三栏 CSS Grid 布局
- [ ] 侧栏：标的列表 + 宏观分类树（纯 HTML/CSS 折叠）
- [ ] 内容区：主图 + 2 副图容器
- [ ] 诊断栏：评分卡片 + 指标面板
- [ ] 底部工具栏：时间范围 / 叠加开关 / 副图选择
- [ ] lightweight-charts CDN 引入 + 初始化

### Phase 3 — K 线图渲染
- [ ] CandlestickSeries 渲染 OHLC
- [ ] MA 叠加层（LineSeries，多色）
- [ ] BB 叠加层（三线 + 半透明填充）
- [ ] SuperTrend 叠加层
- [ ] RSI / MACD 副图（LineSeries + HistogramSeries）
- [ ] crosshair 回看 + 诊断联动刷新
- [ ] 鼠标滚轮缩放 + 拖拽平移

### Phase 4 — 宏观图渲染
- [ ] 时序折线多系列叠加
- [ ] 期限结构柱状图（仅 rates/tips）
- [ ] 系列 toggle 交互

### Phase 5 — 打磨
- [ ] 响应式布局（窗口缩放自适应）
- [ ] 暗色/亮色主题切换
- [ ] 加载状态 spinner
- [ ] 错误状态处理
- [ ] 键盘快捷键（保留 TUI 惯性）

## 不变的部分

以下代码和资产 **零改动**：

```
src/analyze.py          ← analyze() 返回 dict，天然 JSON
src/cache.py            ← load_or_compute() 不动
src/macro.py            ← derive_macro() 不动
src/indicators.py       ← compute_all_indicators() 不动
src/config.py           ← IBKR_SYMBOLS / FRED_SERIES 不动
src/tui/                ← 保留，随时可回退
data/                   ← CSV / cache parquet 不动
tests/                  ← 现有测试不动
```

## 依赖变化

```diff
# pyproject.toml
- textual >= 8.2.8
- textual-plotext >= 1.0.1
+ fastapi >= 0.100
+ uvicorn >= 0.30
```

## 启动方式

```bash
uv run uvicorn src.server:app --reload --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

---

**总代码量预估**：`server.py` ~150 行 + `web/index.html` ~500 行 ≈ **650 行新代码**，替代现有 TUI 的 1,289 行。
