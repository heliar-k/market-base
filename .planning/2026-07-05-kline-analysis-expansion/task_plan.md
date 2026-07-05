# Task Plan: K线分析平台 2.0 — 四方向扩展

## Goal
将现有的 K 线分析 Web 应用扩展为支持跨周期关联、个股增强分析、流动性热力图和综合仪表盘的宏观分析平台。

## Current Phase
Phase 1 (Requirements & Discovery) — 完成

## Phases

### Phase 1: Requirements & Discovery ✅
- [x] 代码库全面审查
- [x] 四方向需求分析
- [x] 架构设计
- [x] 技术选型评估
- [x] 输出产品规格文档 (product-spec.md)
- **Status:** complete

### Phase 2: Sprint 0 — 基础设施
- [ ] 拆分 index.html → CSS + JS 模块
- [ ] 新增顶部标签页导航
- [ ] 拆分 tech-view.js 和 macro-view.js
- [ ] 新增 API 端点框架
- **Status:** pending

### Phase 3: Sprint 1 — 跨周期关联视图 (#1)
- [ ] 后端 /api/macro/correlate
- [ ] 前端指标选择器 + 双 Y 轴图表
- [ ] 归一化模式 (raw/pct/zscore)
- [ ] 预设模板
- [ ] 保存自定义预设
- **Status:** pending

### Phase 4: Sprint 2 — 流动性热力图 (#3)
- [ ] 后端 /api/liquidity/overview
- [ ] 后端 /api/liquidity/compare-spx
- [ ] 摘要卡片 + 分项堆叠面积图
- [ ] RRP vs 准备金跷跷板图
- [ ] TGA 波动图
- [ ] NET_LIQUIDITY + SPX 叠加
- **Status:** pending

### Phase 5: Sprint 3 — 个股 K 线增强 (#2)
- [ ] 多周期后端支持 (1d/1wk/1mo)
- [ ] 新增技术指标 (DMI/CCI/Stoch/OBV/ATR)
- [ ] IBKR 管道增强
- [ ] GEX 叠加端点 + 前端
- [ ] 周期切换交互
- **Status:** pending

### Phase 6: Sprint 4 — 综合仪表盘 (#4)
- [ ] 后端 /api/dashboard/summary
- [ ] 后端 /api/watchlist (GET/POST)
- [ ] 摘要卡片 + 迷你图表网格
- [ ] 自选股监控
- [ ] 仪表盘设为默认首页
- **Status:** pending

### Phase 7: Sprint 5 — 打磨上线
- [ ] CSS 统一
- [ ] 错误处理 + Loading 状态
- [ ] 键盘快捷键
- [ ] 性能优化
- [ ] 产品文档 + 回归测试

### Phase 8: Delivery
- [ ] 全量回归测试
- [ ] 交付产品规格文档给用户确认

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 保持纯 HTML/JS | 当前 761 行，拆分 ES modules 足够，避免 React 构建链 |
| 后端预计算指标 | 已有 numpy/pandas 基础设施，前端保持轻量 |
| CSV 数据层不变 | 离散分析工具，不需要数据库 |
| 先做模块一（关联）再模块三（流动性）| 复用现有 FRED 数据最多，ROI 最高 |
| Dashboard 最后做 | 依赖其他三个模块就绪 |
| 不引入 WebSocket | 非实时交易场景，无需推送 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| — | — |
