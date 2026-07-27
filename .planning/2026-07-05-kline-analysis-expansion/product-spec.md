# 产品规格文档：K线分析平台 2.0

> 日期：2026-07-05
> 协议版本：1.0
> 基于当前代码库状态：FastAPI + 纯 HTML/JS + Lightweight Charts 4.2.2 + CSV 数据层

---

## 目录

1. [整体架构设计](#1-整体架构设计)
2. [模块一：跨周期关联视图](#2-模块一-跨周期关联视图)
3. [模块二：个股 K 线模块](#3-模块二-个股-k-线模块)
4. [模块三：流动性热力图](#4-模块三-流动性热力图)
5. [模块四：综合仪表盘](#5-模块四-综合仪表盘)
6. [API 设计](#6-api-设计)
7. [数据管道](#7-数据管道)
8. [技术选型分析](#8-技术选型分析)
9. [开发优先级与 Sprint 拆解](#9-开发优先级与-sprint-拆解)
10. [附录](#10-附录)

---

## 1. 整体架构设计

### 1.1 前端架构：保持纯 HTML/JS，模块化演进

**决策：保持单文件渐进拆分，不引入框架**

| 因素 | 评估 |
|------|------|
| 当前代码量 | 761 行单文件，复杂度可控 |
| 依赖复杂度 | 仅 Lightweight Charts CDN，无 npm/build 链 |
| 用户画像 | 单人开发者，macOS，快速迭代优先 |
| 新增模块数 | 4 个方向，代码量估计增至 2500-4000 行 |
| 框架成本 | React 需引入 Vite/Webpack + npm 生态，增加构建负担 |

**推荐路径**：拆分为多 JS 模块文件（ES modules）+ 统一入口

```
static/
├── index.html              # 主入口，标签页骨架 + CSS
├── css/
│   └── app.css             # 提取内联 CSS → 外部文件
├── js/
│   ├── app.js              # 入口：初始化标签页路由、全局状态
│   ├── charts-common.js    # 通用图表工具（createChart, addLine, 日期过滤等）
│   ├── tech-view.js        # 现有技术面 K 线逻辑（从 index.html 拆出）
│   ├── macro-view.js       # 现有宏观面板逻辑（从 index.html 拆出）
│   ├── cross-correlation.js # 模块一：跨周期关联视图
│   ├── stock-kline.js      # 模块二：个股 K 线 + 技术指标 + GEX 叠加
│   ├── liquidity-heatmap.js # 模块三：流动性热力图
│   └── dashboard.js        # 模块四：综合仪表盘
└── index.html              # 仅保留骨架 HTML + CSS（精简至 ~200 行）
```

**好处**：
- 不改变部署方式（仍是 static files）
- 不引入 npm 依赖
- 每个模块独立文件，支持并行开发
- 复用通用图表工具函数

### 1.2 标签页路由设计

当前侧边栏有"技术"/"宏观"两个标签页。扩展为五标签导航：

```
┌─────────────────────────────────────┐
│  🏠 仪表盘                           │ ← 模块四：默认首页
│  📈 技术面                           │ ← 现有 K 线分析
│  🌍 宏观                             │ ← 现有宏观面板
│  🔗 关联                             │ ← 模块一：跨周期关联
│  💧 流动性                           │ ← 模块三：流动性热力图
│  🔧 个股                             │ ← 模块二：个股 K 线（保留侧边栏选股）
└─────────────────────────────────────┘
```

导航策略：顶部 tab bar 替代当前 sidebar tab，释放 sidebar 空间用于选股/选指标。

### 1.3 新布局方案

```
┌──────────┬────────────────────────────────┬────────────┐
│          │  ┌─────────标签页导航─────────┐  │            │
│ 侧边栏   │  │ 仪表盘 │ 技术 │ 宏观 │ ... │  │  诊断面板  │
│ (选股/   │  ├───────────────────────────┤  │  (可折叠)  │
│  选指标) │  │                           │  │            │
│          │  │      主内容区域            │  │            │
│          │  │      (模块渲染区)          │  │            │
│          │  │                           │  │            │
│          │  └───────────────────────────┘  │            │
│          │  ┌───────────────────────────┐  │            │
│          │  │      状态栏               │  │            │
└──────────┴──┴───────────────────────────┴──┴────────────┘
```

---

## 2. 模块一：跨周期关联视图

### 2.1 产品目标

让用户从不同 FRED 分类中选取指标，叠加到同一张图上进行跨类别对比分析。

### 2.2 UI 布局

```
┌──────────────────────────────────────────────┐
│  工具栏: [通胀vs利率▾] [利差vs衰退▾] [自定义]  │  ← 预设模板下拉
│  日期筛选: [1M|3M|6M|1Y|2Y|5Y|10Y|30Y|All]   │
├──────────────┬─────────────────┬─────────────┤
│  指标选择器   │                 │             │
│  ┌──────────┐│   主图表        │  指标配置    │
│  │通胀      ││  (关联叠加图)   │  ┌─────────┐ │
│  │ □ CPI    ││                 │  │ CPI     │ │
│  │ □ PCE    ││  ┌───────────┐  │  │  左Y轴▾ │ │
│  │ □ CoreCPI││  │ CPI(左Y)  │  │  │  颜色 ■ │ │
│  ├──────────┤│  │ FEDFUNDS  │  │  │  线宽 2 │ │
│  │利率      ││  │ (右Y)     │  │  ├─────────┤ │
│  │ □ FFR    ││  └───────────┘  │  │ FFR     │ │
│  │ □ 2s10s  ││                 │  │  右Y轴▾ │ │
│  │ □ SOFR   ││                 │  │  颜色 ■ │ │
│  ├──────────┤│                 │  └─────────┘ │
│  │流动性    ││                 │             │
│  │ ...      ││                 │ [保存为预设] │
│  └──────────┘│                 │             │
└──────────────┴─────────────────┴─────────────┘
```

### 2.3 交互流程

**预设模板模式**：
1. 用户点击工具栏预设（如"通胀vs利率"）
2. 自动选中 CPI、PCE、CORE_CPI、FEDFUNDS、DGS10
3. CPI/PCE/CORE_CPI 挂左 Y 轴，FEDFUNDS/DGS10 挂右 Y 轴
4. 图表自动渲染，颜色按系列自动分配

**自定义模式**：
1. 用户在左侧指标选择器中勾选/取消指标（按分类分组，与现有宏观面板同源）
2. 拖拽指标到主图区域（可选，初版可用勾选替代）
3. 右侧面板中为每个已选指标配置：Y 轴（左/右）、颜色、线型
4. 支持"保存为预设"存储到 localStorage

**数据归一化选项**：
- 原始值（默认）- 各自按原单位显示
- 百分比变化 - 从起始日计算累计变化率
- 标准化 (Z-Score) - 均值0标准差1

### 2.4 预设模板定义

```javascript
const CORRELATION_TEMPLATES = {
  "inflation_vs_rates": {
    name: "通胀 vs 利率",
    leftAxis: ["CPI", "PCE", "CORE_CPI"],
    rightAxis: ["FEDFUNDS", "DGS10"],
    normalize: "percent_change"
  },
  "spread_vs_recession": {
    name: "利差 vs 衰退阴影",
    leftAxis: ["SPREAD_2S10S"],
    rightAxis: ["UNRATE", "UMCSENT"],
    normalize: "raw"
  },
  "liquidity_vs_volatility": {
    name: "流动性 vs 波动率",
    leftAxis: ["NET_LIQUIDITY", "WALCL"],
    rightAxis: ["VIX", "NFCI"],
    normalize: "zscore"
  }
};
```

### 2.5 数据流

```
用户选择模板/勾选指标
  → POST /api/macro/correlate (选定指标列表)
  → 后端从各分类 CSV 读取并合并到统一 DataFrame
  → 可选归一化处理
  → 返回统一时间序列 [{date, CPI, FEDFUNDS, ...}]
  → 前端按 Y 轴分组渲染多条 LineSeries
```

### 2.6 技术要点

- **多 Y 轴**：Lightweight Charts 支持多个 priceScale，用 `priceScaleId` 绑定
- **颜色分配**：固定色板 10 色 + 超出时 HSL 循环
- **tooltip**：复用现有 macro tooltip 模式，显示多系列值
- **localStorage 预设**：JSON 序列化，预设名 + 指标列表 + 配置

---

## 3. 模块二：个股 K 线模块

### 3.1 产品目标

在现有技术面 K 线基础上，新增个股日线/周线、更多技术指标叠加、可选的期权 GEX 数据叠加。

### 3.2 UI 布局（复用现有技术面视图，增强）

```
┌──────────────────────────────────────────────────┐
│ 工具栏: [日线▾|周线▾|月线▾] [1M|3M|6M|1Y|2Y|All]  │
│ 叠加: ☑MA5 ☑MA10 ☑MA20 □MA60 □MA120              │
│       ☑BB ☑SuperTrend □GEX(期权墙) □DMI           │  ← 新增 GEX/DMI
├──────────────────────────────────────────────────┤
│              主图表 (Candlestick + Overlays)       │
│              ┌── GEX Call Wall ── (虚线)           │
│              ├── GEX Put Wall  ── (虚线)           │
│              └── 0 GEX Line    ── (零线)           │
├──────────────────────────────────────────────────┤
│              成交量                               │
├──────────────────────────────────────────────────┤
│              RSI (14)                            │
├──────────────────────────────────────────────────┤
│              MACD                                │
└──────────────────────────────────────────────────┘
```

### 3.3 数据流

**K 线数据**（现有流程增强）：
```
用户选择股票 + 时间周期
  → GET /api/kline/{symbol}?interval=1d|1wk|1mo&range=2y
  → 后端读取 data/stocks/{symbol}.csv 或 data/indices/{symbol}.csv
  → 根据 interval 重采样（日线→周线/月线）
  → 计算技术指标（MA/BB/RSI/MACD/DMI/SuperTrend/Stoch/CCI/ATR）
  → 返回 { candles: [...], indicators: {...}, volume: [...] }
```

**GEX 叠加**（新增）：
```
用户勾选 GEX 叠加
  → GET /api/gex/{symbol}?as_of=latest
  → 后端读取 data/gex/{symbol}_gex_*.csv（最新快照）
  → 解析 gamma 按 strike 分布
  → 识别 Call Wall（gamma 最大正值的 strike）
  → 识别 Put Wall（gamma 最大负值的 strike）
  → 返回 { callWall: price, putWall: price, zeroGamma: price, profile: [...] }
  → 前端在蜡烛图上叠加水平线/虚线
```

### 3.4 新增技术指标

| 指标 | 系列名 | 参数 | 显示方式 |
|------|--------|------|----------|
| DMI | ADX/+DI/-DI | 周期14 | 叠加在 RSI 图或独立子图 |
| CCI | CCI | 周期20 | 独立子图，±100 参考线 |
| Stoch | %K/%D | 14,3,3 | 独立子图，20/80 分界线 |
| OBV | OBV | — | 叠加在成交量图 |
| ATR | ATR | 周期14 | 独立子图 |

### 3.5 IBKR 数据拉取增强

现有 `ibkr_fetcher.py` 已支持 `ib_insync`。需扩展：

1. **多周期支持**：同时拉取日线和周线
2. **增量更新**：按 last_date 增量追加
3. **用户自选股管理**：新增 `watchlist.json` 配置文件
4. **批量拉取脚本**：`./bin/fetch_ibkr --watchlist` 一键拉取自选

### 3.6 GEX 数据管道（已有基础）

现有 `compute_gex.py` 已支持从 IBKR 拉取 gamma + yfinance OI 计算 GEX。需要：
- 定期快照（已有 `data/gex/` 目录）
- 为 K 线叠加提供简化端点
- 若快照过期（超过1天），前端显示"数据过期"提示

---

## 4. 模块三：流动性热力图

### 4.1 产品目标

可视化美联储资产负债表结构变化，提供系统流动性（System Liquidity）全景视角。

### 4.2 现有数据基础

`data/fed_balance/liquidity.csv` 已有字段：
- `RRP` - 隔夜逆回购规模（百万美元）
- `TGA` - 财政部一般账户（百万美元）
- `RESERVES` - 银行准备金余额（百万美元）
- `SOFR` - 担保隔夜融资利率
- `IORB` - 准备金余额利率
- `SOFR_IORB_SPREAD` - 利差（已计算）
- `NET_LIQUIDITY` - 净流动性 = WALCL - RRP - TGA（已计算）

### 4.3 UI 布局

```
┌──────────────────────────────────────────────────┐
│  日期筛选: [3M|6M|1Y|2Y|5Y|All]                   │
├──────────────────────────────────────────────────┤
│  ┌─ 摘要卡片 ───────────────────────────────────┐ │
│  │ 净流动性     │ RRP       │ TGA        │ 准备金  │ │
│  │ $5,842B     │ $2.2B    │ $880B      │ $2,967B │ │
│  │ ▲ +1.2% WoW │ ▼ -60%   │ ▼ -4%      │ ▲ +0.5% │ │
│  └──────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ WALCL 趋势总览   │  │ 分项堆叠面积图           │ │
│  │ (折线，带阴影)   │  │ █ WALCL █ RRP █ TGA     │ │
│  │                 │  │ █ RESERVES █ Other       │ │
│  │         ╱‾‾‾‾╲  │  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │ │
│  │   ╱‾‾‾╱       ╲ │  │ ▓▓▓░░░░░░░░░░░░░░░░░░ │ │
│  │  ╱              │  │ ▓░░░██████████████████ │ │
│  └─────────────────┘  └─────────────────────────┘ │
├──────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ RRP vs 准备金    │  │ TGA 账户余额波动          │ │
│  │ (跷跷板双线图)   │  │ (折线 + 税收日历标记)     │ │
│  │ ── RRP          │  │ ── TGA                   │ │
│  │ ── RESERVES     │  │ │  ▁ ▂ ▃ ▄ ▅ ▆ ▇ ▆ ▅   │ │
│  │ 此消彼长关系     │  │ │  季度缴税期阴影          │ │
│  └─────────────────┘  └─────────────────────────┘ │
├──────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────┐ │
│  │ NET_LIQUIDITY 汇总线 (大图，带 SPX 叠加可选)   │ │
│  │ ── NET_LIQUIDITY (左Y轴)                     │ │
│  │ - - SPX (右Y轴，可选叠加)                      │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

### 4.4 数据流

```
页面加载
  → GET /api/liquidity/overview
  → 后端读取 data/fed_balance/liquidity.csv
  → 计算衍生字段（WoW 变化率等）
  → 返回统一 JSON

摘要卡片
  → 取最新一行数据 + WoW 对比

分项堆叠面积图
  → 选取 WALCL, RRP, WTREGEN(TGA), WRESBAL(RESERVES) 列
  → 前端用 AreaSeries 堆叠

RRP vs 准备金
  → 双 LineSeries，左 Y 轴

TGA 波动
  → LineSeries + 标注季度纳税日（硬编码日期标记）

NET_LIQUIDITY vs SPX
  → 双 Y 轴 LineSeries
  → 可选从 stocks/SPX.csv 加载 SPX 历史
```

### 4.5 需要的 FRED 系列补充

当前 `liquidity` 分类的 FRED 系列：
```python
"liquidity": {
    "NFCI": "NFCI",
    "RRPONTSYD": "RRPONTSYD",
    "WTREGEN": "WTREGEN",
    "WRESBAL": "WRESBAL",
    "WALCL": "WALCL",
}
```

`fed_balance/liquidity.csv` 是单独拉取的数据源（可能来自不同管道）。应统一：让 FRED 也拉取 WALCL 分项或直接用 fed_balance 数据。

---

## 5. 模块四：综合仪表盘

### 5.1 产品目标

首页 Dashboard，汇总关键宏观指标 + 自选股快照 + 系统状态。

### 5.2 UI 布局

```
┌──────────────────────────────────────────────────────┐
│  K线分析平台 2.0                   最后更新: 13:45:22  │
├─────────────┬──────────┬──────────┬──────────┬───────┤
│ S&P 500     │ VIX      │ 10Y UST  │ FED FUNDS│ DXY   │
│ 15,234.56   │ 15.42    │ 3.85%    │ 3.75%    │ 102.3 │
│ ▲ +1.23%    │ ▼ -2.1%  │ ▼ -5bp   │ ◀ 不变   │ ▲ 0.3%│
├─────────────┴──────────┴──────────┴──────────┴───────┤
│  ┌───────────────────┐  ┌───────────────────────────┐ │
│  │ 市场情绪          │  │ 流动性概览                 │ │
│  │ ┌────┬────┬────┐  │  │ 净流动性: $5,842B ▲      │ │
│  │ │恐慌│中性│贪婪│  │  │ RRP↘  TGA↘  RESERVES→   │ │
│  │ └────┴────┴────┘  │  │ ████████████░░░░░ 80%   │ │
│  └───────────────────┘  └───────────────────────────┘ │
│  ┌───────────────────┐  ┌───────────────────────────┐ │
│  │ 收益率曲线 (迷你) │  │ 自选股监控                 │ │
│  │ 2s10s: -18bp     │  │ AAPL $245.32 ▲1.2%       │ │
│  │ 3m10y: -25bp     │  │ NVDA $987.45 ▲3.5%       │ │
│  │ [迷你期限结构图]  │  │ TSLA $312.10 ▼0.8%       │ │
│  └───────────────────┘  └───────────────────────────┘ │
├──────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────┐ │
│  │ 关键图表联动 (小图预览，点击进入对应模块)         │ │
│  │ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │ │
│  │ │ NET_LIQ │ │ CPI/YoY │ │ SPX K线 │ │ 利差趋势 │ │ │
│  │ │ vs SPX  │ │ vs FFR  │ │ (迷你)  │ │ 2s10s   │ │ │
│  │ │ (迷你)  │ │ (迷你)  │ │         │ │ (迷你)  │ │ │
│  │ └─────────┘ └─────────┘ └─────────┘ └─────────┘ │ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 5.3 摘要卡片数据源

| 卡片 | 数据来源 | 更新频率 |
|------|----------|----------|
| S&P 500 | `data/indices/SPX.csv` 最新收盘 | 每日 |
| VIX | `data/fred/volatility/volatility.csv` VIXCLS | 每周 |
| 10Y UST | `data/fred/rates/rates.csv` DGS10 | 每周 |
| FEDFUNDS | `data/fred/rates/rates.csv` FEDFUNDS | 每周 |
| DXY | `data/fred/fx/fx.csv` DXY | 每周 |
| 市场情绪 | `data/fred/sentiment/sentiment.csv` + VIX | 混合 |
| 净流动性 | `data/fed_balance/liquidity.csv` | 每周 |
| 自选股 | `data/stocks/*.csv` 最新收盘 | 每日 |

### 5.4 数据流

```
Dashboard 加载
  → GET /api/dashboard/summary
  → 后端并行读取各数据源最新行
  → 计算变化率（WoW / MoM）
  → 返回统一 JSON

自选股监控
  → GET /api/watchlist
  → 后端读取 watchlist.json 配置
  → 从各 stock CSV 取最新行
  → 返回 [{symbol, price, change_pct, volume, ...}]

迷你图表
  → 复用各模块 API 端点（缓存友好）
  → 前端用 Lightweight Charts 创建小型画布（height: 120px）
```

---

## 6. API 设计

### 6.1 现有端点（保持不变）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/symbols` | IBKR 标的列表 |
| GET | `/api/kline/{symbol}` | K 线数据（日线） |
| GET | `/api/diag/{symbol}` | 诊断分析 |
| GET | `/api/macro/categories` | FRED 分类列表 |
| GET | `/api/macro/{category}` | FRED 分类时序数据 |
| GET | `/api/macro/{category}/term` | 期限结构 |

### 6.2 新增端点

#### 模块一：跨周期关联

```
GET /api/macro/correlate
  Query: series=CPI,PCE,FEDFUNDS,DGS10&normalize=raw|pct|zscore
  Returns: [{date, CPI, PCE, FEDFUNDS, DGS10, ...}]

  实现：遍历 series 列表，从各 FRED 分类 CSV 提取列，按日期 outer join，
  归一化处理，返回合并后的 DataFrame。
```

#### 模块二：个股 K 线

```
GET /api/kline/{symbol}
  新增 Query: interval=1d|1wk|1mo  (默认 1d)
  新增 Query: range=1m|3m|6m|1y|2y|5y|all  (默认 2y)
  增强 Response: 包含预计算的 MA/BB/RSI/MACD/DMI 等指标

GET /api/gex/{symbol}
  Query: as_of=latest|2026-07-05
  Returns: {
    callWall: number|null,    # gamma 最大正值的 strike 价
    putWall: number|null,     # gamma 最大负值的 strike 价
    zeroGamma: number|null,   # GEX 总和过零的 strike
    spotPrice: number,
    profile: [{strike, gamma, OI, GEX}],  # 按行权价分布
    updatedAt: "2026-07-05T13:45:22"
  }
```

#### 模块三：流动性热力图

```
GET /api/liquidity/overview
  Returns: {
    summary: {
      netLiquidity: { value, changeWoW, changePct },
      rrp: { value, changeWoW, changePct },
      tga: { value, changeWoW, changePct },
      reserves: { value, changeWoW, changePct },
      sofrIorbSpread: { value, changeWoW },
      walcl: { value, changeWoW, changePct },
    },
    timeseries: [{date, RRP, TGA, RESERVES, WALCL, SOFR, IORB,
                   SOFR_IORB_SPREAD, NET_LIQUIDITY, ...}],
    components: [{date, WALCL, RRP, TGA, RESERVES, OTHER}],
    updatedAt: "2026-07-05"
  }

GET /api/liquidity/compare-spx
  Query: normalize=true
  Returns: [{date, NET_LIQUIDITY, SPX}]

  实现：合并 liquidity.csv 和 indices/SPX.csv，可选归一化
```

#### 模块四：仪表盘

```
GET /api/dashboard/summary
  Returns: {
    spx:     { price, change, changePct },
    vix:     { value, change },
    ust10y:  { yield: 3.85, changeBp: -5 },
    fedFunds:{ rate: 3.75, changeBp: 0 },
    dxy:     { value, changePct },
    sentiment: { score: 65, label: "greed" },
    liquidity: { netLiquidity, trend: "up" },
    yieldCurve: { spread2s10s: -18, spread3m10y: -25 },
    updatedAt: "2026-07-05T13:45:22"
  }

GET /api/watchlist
  Returns: [
    { symbol, name, type, lastPrice, changePct, volume, updatedAt },
    ...
  ]

POST /api/watchlist
  Body: { symbols: ["AAPL", "NVDA", "TSLA"] }
  保存自选股到 watchlist.json
```

### 6.3 API 实现原则

1. **无状态**：所有端点纯读，状态在前端（localStorage/watchlist.json）
2. **CSV 优先**：不引入数据库，数据层保持 CSV
3. **增量计算**：技术指标在后端预计算（已有 `indicators.py` 基础）
4. **容错**：CSV 缺失列时优雅降级，不报 500
5. **缓存**：复用现有 `cache.py` 的 `load_or_compute` 模式

---

## 7. 数据管道

### 7.1 IBKR 数据管道

**现状**：
- `src/fetchers/ibkr_fetcher.py`：使用 `ib_insync` 拉取日线 OHLCV
- 输出到 `data/stocks/` 和 `data/indices/`
- 命令行：`./bin/fetch_ibkr --symbols AAPL,TSLA --days 365`

**增强计划**：

```
ibkr_fetcher.py (增强)
├── 多周期拉取: --interval 1d|1wk|1mo
├── 自选股批量: --watchlist (读取 watchlist.json)
├── 增量更新: 检查 CSV 最新日期，只拉增量
├── 调度: crontab 每日自动拉取
└── 错误处理: IB Gateway 未连接时降级到 yfinance

新增: ibkr_options_fetcher.py (GEX 数据)
├── 每周拉取一次 GEX 快照
├── 输出到 data/gex/{symbol}_gex_{date}.csv
└── 调用现有 compute_gex.py 逻辑
```

### 7.2 FRED 数据管道（现有，保持不变）

```
fred_fetcher.py (现有)
→ 从 FRED API 拉取 9 大分类数据
→ 输出到 data/fred/{category}/{category}.csv
→ 周线频率
```

### 7.3 Fed 资产负债表管道

**现状**：`data/fed_balance/liquidity.csv` 已有数据但来源未知

**增强**：
- 新增 `src/fetchers/fed_balance_fetcher.py`（或扩展现有）
- 从 FRED 拉取完整分项：WALCL, WALCL_BORROW, WORAL, WSRECAL, WLRAL, WTREGEN, WDTGAL, WCTCL, WRESBAL, RRPONTSYD
- 输出到 `data/fed_balance/`（替换或增补现有）
- 每周更新

### 7.4 数据更新调度

```bash
# crontab 示例（macOS）
# 每周一 08:00 拉 FRED 数据
0 8 * * 1 cd /path/to/project && uv run python -m src.fetchers.fred_fetcher

# 每日 16:30 (收盘后) 拉 IBKR K 线
30 16 * * 1-5 cd /path/to/project && ./bin/fetch_ibkr --watchlist

# 每周五 17:00 拉 GEX 快照
0 17 * * 5 cd /path/to/project && uv run python src/compute_gex.py --symbol AAPL,SPY
```

---

## 8. 技术选型分析

### 8.1 前端框架：保持纯 HTML/JS ✅

| 因素 | 纯 HTML/JS (推荐) | React + Vite |
|------|-------------------|--------------|
| 学习曲线 | 低，当前已掌握 | 中，需 State/Effect/Hooks |
| 构建步骤 | 无，保存即刷新 | npm install → dev server |
| 部署复杂度 | 零，StaticFiles 即可 | 需构建产出 dist/ |
| 图表库兼容 | Lightweight Charts 原生 JS | 需 wrapper 或 ref 处理 |
| 代码组织 | ES modules 按文件拆分 | 组件树天然模块化 |
| 复杂度上限 | 2500-4000 行可管理 | 适合更大规模 |
| 迁移成本 | 从当前代码渐进拆分 | 重写整个前端 |

**结论**：保持纯 HTML/JS + ES modules。达到 4000 行以上时再评估 React。

### 8.2 图表库：保持 Lightweight Charts ✅

- 已掌握 API
- 支持多 priceScale（关键需求）
- 性能优于 ECharts（金融数据场景）
- 不需要 D3 级别的自定义能力

### 8.3 数据存储

| 数据类型 | 存储方式 | 原因 |
|----------|----------|------|
| 历史 OHLCV | CSV (data/stocks/) | 现有，简单可靠 |
| FRED 宏观 | CSV (data/fred/) | 现有，周线数据 |
| 期权 GEX | CSV (data/gex/) | 快照型数据 |
| 用户自选股 | JSON (watchlist.json) | 小规模，结构化 |
| 用户预设模板 | localStorage | 纯前端，跨设备不共享 |
| 计算缓存 | Parquet (data/cache/) | 已有机制 |

### 8.4 关键权衡

| 取舍 | 选择 | 原因 |
|------|------|------|
| 计算位置 | 后端预计算 | 指标计算已有 Python 库（numpy/pandas），避免前端重复 |
| 数据拉取 | 服务端脚本 + crontab | 保持前端轻量，避免浏览器直连 IBKR |
| 实时 vs 延迟 | 延迟（T+1 或周级） | 目标分析场景，非交易执行 |
| 响应式 | 先做桌面端 | 主力在 macOS 桌面浏览器使用 |

---

## 9. 开发优先级与 Sprint 拆解

### 优先级排序逻辑

1. **模块一（跨周期关联）**：复用现有 FRED 数据 + API，后端工作量小，前端新视图，ROI 最高
2. **模块三（流动性热力图）**：已有数据基础，UI 独立，不与现有模块耦合
3. **模块二（个股 K 线增强）**：在现有技术面基础上增量改进，需 IBKR 管道增强
4. **模块四（综合仪表盘）**：依赖其他模块就绪，作为最终整合层

### Sprint 0：基础设施（第 1 周）

**目标**：前端模块化拆分 + 新 API 框架建立

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 0.1 | 拆分 index.html → CSS + JS 模块 | `css/app.css`, `js/app.js`, `js/charts-common.js` | 4h |
| 0.2 | 新增顶部标签页导航 | 五标签页骨架，路由切换 | 3h |
| 0.3 | 拆分现有技术面到 `js/tech-view.js` | 逻辑抽离，保持功能完整 | 3h |
| 0.4 | 拆分现有宏观到 `js/macro-view.js` | 逻辑抽离，保持功能完整 | 3h |
| 0.5 | 新增 API 端点框架 + 测试 | `server.py` 新增路由占位 | 2h |

**验收标准**：
- 现有所有功能仍正常工作
- 页面拆分后加载无报错
- 五个标签页可切换（后三个显示"开发中"）

### Sprint 1：跨周期关联视图（第 2 周）

**目标**：完成模块一全部功能

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 1.1 | 后端 `/api/macro/correlate` | 跨分类合并 + 归一化 | 3h |
| 1.2 | 前端 `js/cross-correlation.js` 基础 | 指标选择器 + 主图表 | 4h |
| 1.3 | 双 Y 轴渲染 | 左 Y 轴 + 右 Y 轴分组 | 3h |
| 1.4 | 三种归一化模式 | raw / pct_change / zscore | 2h |
| 1.5 | 预设模板 | 通胀vs利率、利差vs衰退、流动性vs波动率 | 2h |
| 1.6 | 保存/加载自定义预设 | localStorage | 1h |
| 1.7 | 测试 + 调试 | 边界情况（缺省、单系列等） | 2h |

**验收标准**：
- 可从不同分类选取指标叠加显示
- 左右 Y 轴正确分组和刻度
- 预设模板一键切换
- 自定义配置可保存

### Sprint 2：流动性热力图（第 3 周）

**目标**：完成模块三全部功能

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 3.1 | 后端 `/api/liquidity/overview` | 摘要 + 时序 + 分项 | 3h |
| 3.2 | 后端 `/api/liquidity/compare-spx` | 合并 SPX 数据 | 2h |
| 3.3 | 前端仪表盘式摘要卡片 | 顶部 5 卡片 + WoW 变化 | 3h |
| 3.4 | WALCL 趋势 + 分项堆叠面积图 | AreaSeries 堆叠 | 4h |
| 3.5 | RRP vs 准备金跷跷板图 | 双线对比 | 2h |
| 3.6 | TGA 波动图 + 税收日历标记 | 折线 + 竖线标记 | 2h |
| 3.7 | NET_LIQUIDITY + SPX 叠加大图 | 双 Y 轴联动 | 2h |
| 3.8 | 测试（含边界情况） | 数据缺失、单点等 | 2h |

**验收标准**：
- 所有子图正确渲染
- 摘要卡片显示最新数据和周变化
- 日期筛选正确过滤
- 颜色方案协调统一

### Sprint 3：个股 K 线增强（第 4 周）

**目标**：增强现有技术面分析 + 集成 GEX

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 2.1 | 后端多周期支持 | interval 参数 + 重采样 | 3h |
| 2.2 | 后端新增指标计算 | DMI, CCI, Stoch, OBV, ATR | 3h |
| 2.3 | 前端增加指标子图 | 新指标独立渲染 | 4h |
| 2.4 | IBKR 管道增强 | 多周期 + 自选股 + crontab | 3h |
| 2.5 | 后端 `/api/gex/{symbol}` | GEX 摘要端点 | 3h |
| 2.6 | 前端 GEX 叠加 | Call/Put Wall 虚线叠加 | 3h |
| 2.7 | 周期间切换按钮 | 日线/周线/月线切换 | 1h |
| 2.8 | 测试 | 指标准确性 + GEX 显示 | 2h |

**验收标准**：
- 日线/周线/月线切换可用
- 新增技术指标正确渲染
- GEX Call/Put Wall 正确叠加在蜡烛图上
- GEX 数据过期时有提示

### Sprint 4：综合仪表盘（第 5 周）

**目标**：完成模块四，整合所有模块

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 4.1 | 后端 `/api/dashboard/summary` | 汇总多源数据 | 4h |
| 4.2 | 后端 `/api/watchlist` (GET/POST) | 自选股 CRUD | 2h |
| 4.3 | 前端摘要卡片 | 顶部关键指标卡片 | 3h |
| 4.4 | 前端迷你图表网格 | 多个 120px 小图 | 4h |
| 4.5 | 前端自选股监控 | 表格 + 涨跌颜色 | 2h |
| 4.6 | 后端 watchlist.json 配置 | 自选股持久化 | 1h |
| 4.7 | 仪表盘默认首页 | 启动时默认显示 | 1h |
| 4.8 | 联调 + 整体测试 | 跨模块联动 | 3h |

**验收标准**：
- 仪表盘作为默认首页
- 所有摘要卡片显示最新数据
- 迷你图表可点击跳转到对应模块
- 自选股列表实时更新涨跌

### Sprint 5：打磨与上线（第 6 周）

| # | 任务 | 产出 | 估时 |
|---|------|------|------|
| 5.1 | 全平台 CSS 统一 | 颜色方案、字体、间距一致 | 3h |
| 5.2 | 错误处理 + Loading 状态 | 各模块统一错误提示 | 2h |
| 5.3 | 键盘快捷键 | 标签页切换快捷键 | 1h |
| 5.4 | 性能优化 | 按需加载标签页、图表懒初始化 | 2h |
| 5.5 | 产品文档 | README 更新 | 1h |
| 5.6 | 整体回归测试 | 全功能 smoketest | 2h |

---

## 10. 附录

### A. 文件清单（预计新增/修改）

```
新增文件：
  static/css/app.css                    (~400 行)  全局样式
  static/js/app.js                      (~150 行)  入口 + 标签页路由
  static/js/charts-common.js            (~200 行)  通用图表工具
  static/js/cross-correlation.js        (~350 行)  模块一
  static/js/stock-kline.js              (~400 行)  模块二（增强技术面）
  static/js/liquidity-heatmap.js        (~350 行)  模块三
  static/js/dashboard.js                (~300 行)  模块四
  data/watchlist.json                   (~100 字节) 自选股配置
  src/fetchers/fed_balance_fetcher.py   (增强/新建)
  tests/test_api_correlate.py
  tests/test_api_liquidity.py
  tests/test_api_dashboard.py

修改文件：
  static/index.html                     (精简至 ~200 行骨架)
  src/server.py                         (新增 5-8 个端点)
  src/config.py                         (新增 LIQUIDITY_SERIES 配置)
  src/fetchers/ibkr_fetcher.py          (多周期增强)
  src/indicators.py                     (新增 DMI/CCI/Stoch/OBV/ATR)

拆分自 index.html：
  static/js/tech-view.js                (~300 行，从 index.html 提取)
  static/js/macro-view.js               (~400 行，从 index.html 提取)
```

### B. 颜色方案

```
主题色：      #1a73e8 (蓝)
成功/涨：     #26a69a (绿)
危险/跌：     #ef5350 (红)
警告/中性：   #ff9800 (橙)
图表色板：    延续现有 MACRO_COLORS 10 色调色板
背景：        #f5f6f8 (浅灰)
卡片：        #fff (白)
```

### C. 关键技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| IBKR Gateway 不稳定 | K 线数据断更 | yfinance 作为备用数据源 |
| GEX 数据实时性不足 | 期权墙数据滞后 | 前端显示"数据更新于"时间戳 |
| 单文件 HTML 越来越大 | 加载/维护困难 | Sprint 0 提前拆分 |
| Lightweight Charts 多 Y 轴限制 | 最多 2-3 个 scale | 归一化模式折叠到单轴 |
| CSV 数据量增大 | 响应变慢 | 后端做日期筛选 + 采样 |

### D. 不做的事情（v2.1 边界）

- ❌ 实时 WebSocket 推送（非交易场景不需要）
- ❌ 用户登录/多用户（单用户工具）
- ❌ React 重写（除非代码量突破 5000 行）
- ❌ 量化回测引擎（超出分析工具定位）
- ❌ 移动端适配（桌面端优先）
- ❌ 告警/通知推送
- ❌ AI 预测/推荐
