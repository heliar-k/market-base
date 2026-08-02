# TUI → Web 重构计划

## 目标

把现有 Textual TUI 替换为浏览器端 Web 界面，分析引擎（analyze / cache / macro / indicators）**零改动**。

## 架构

```
┌─────────────────────────────────────────────────┐
│  Browser (index.html)                            │
│  ┌──────────┬────────────────────┬─────────────┐ │
│  │ 标的列表  │  lightweight-charts │ 诊断卡片     │ │
│  │ / 宏观树  │  K线 + 叠加 + 副图  │ 评分/均线    │ │
│  │          │                    │  /RSI/MACD   │ │
│  ├──────────┴────────────────────┴─────────────┤ │
│  │ 状态栏 / 键盘快捷键                           │ │
│  └─────────────────────────────────────────────┘ │
└────────────────────┬────────────────────────────┘
                     │ REST API (JSON)
┌────────────────────▼────────────────────────────┐
│  FastAPI (src/server.py)                         │
│  GET /api/symbols          → IBKR_SYMBOLS       │
│  GET /api/kline/{symbol}   → OHLCV + 指标列      │
│  GET /api/diag/{symbol}    → analyze() dict     │
│  GET /api/macro/categories → FRED_SERIES 分类    │
│  GET /api/macro/{category} → FRED CSV + 派生列   │
│  GET /api/macro/{cat}/term → 期限结构快照         │
└────────────────────┬────────────────────────────┘
                     │ 复用现有模块
┌────────────────────▼────────────────────────────┐
│  src/analyze.py   src/cache.py   src/macro.py    │
│  src/indicators.py  src/config.py                │
│  data/{stocks,indices,fred}/*.csv               │
└─────────────────────────────────────────────────┘
```

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | FastAPI | 轻量、async、自动 OpenAPI 文档 |
| 图表 | lightweight-charts v4 | TradingView 开源，原生 candlestick + 叠加 + 副图 |
| 前端 | 纯 HTML/JS/CSS | 零构建，一个文件，CDN 加载库 |
| 主题 | 浅色专业风 | 白底 + 蓝灰辅色 + 绿涨红跌 |

## 分阶段

### Phase 1 — 原型（本次）
- `src/server.py`：FastAPI 6 个端点
- `static/index.html`：单页三栏布局 + K 线图 + 诊断卡片
- 所有数据走现有 CSV / cache 层
- `uv run python -m src.server` 启动，浏览器打开 `localhost:8000`

### Phase 2 — 完善
- 宏观模式 Tab 切换
- 副图指标切换（RSI/MACD/Stoch/CCI/MFI）
- 鼠标 hover tooltip + 滚轮缩放
- 键盘快捷键（←→ 回看，b/m/s 叠加）
- 期限结构图

### Phase 3 — 打磨
- 暗色模式切换
- Panel 可拖拽 resize
- 数据实时刷新
- Docker 化部署

## 关键设计决策

1. **分析引擎不改**：server.py 只是薄薄一层 API wrapper，直接调现有函数
2. **图表库选 lightweight-charts**：原生 candlestick + MA/BB/SuperTrend 叠加 + 副图 indicator pane，API 和现在的 `_draw_main` 结构高度对应
3. **单文件前端**：不引入 npm/webpack/Vite，CDN 加载 lightweight-charts，保持改动成本最低
4. **数据流**：每个请求都走 load_or_compute(cache) → 不引入额外状态层
5. **JSON 全量返回**：OHLCV + 所有指标列一次返回，前端按需渲染，减少 API 往返
