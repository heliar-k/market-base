# Spec：确定性巨头池 Wheel 策略雷达 Web 接入计划

> **状态**：Ready for implementation（可直接交接给新 session 按切片顺序执行）
> **前置完成态**：后端引擎 `src/wheel_scanner.py`（Commit `022a34e5`）与 Barchart 52 周官方 IV Rank 采集（`src/options_structure.py`）已合入 `main` 并通过单测。

---

## 1. 目标与架构决策（Why & Where）

将 `src/wheel_scanner.py` 的扫描结果（17 只确定性巨头池 T0/T1/T2、真实 35d IV / VRP、Barchart 1Y IV Rank、空头企稳过滤、财报/除息排雷、30~45 DTE 推荐期权合约）接入网页端 **`/assets/equities/options/`（`frontend/src/pages/assets/equities/options.astro`）**。

### 核心设计决策：零新增 API 端点，直接内嵌 `options_board()`
- **做法**：在 `src/assets_analysis.py::options_board()` 返回的 dict 中新增顶层键 `"wheel"`（即 `D.wheel = {"snapshot_date": ..., "summary": ..., "rows": [...]}`）。
- **理由**：
  1. `/assets/equities/options.astro` 当前只请求 `/api/assets/equities-options` 一个主端点；若新增第二个端点，按全站守卫 `tests/test_static_single_source.py` 必须改造为 `R.getOpt` 双端点模式，且增加 `src/server.py` 与 `src/export_pages.py` 维护成本。
  2. 直接挂在 `options_board()` 下，`/api/assets/equities-options` 与静态预渲染 `uv run python -m src.export_pages` **自动包含 `wheel` 数据，零额外请求**。

---

## 2. 实施切片（Step-by-Step）

### Step 1：后端数据挂载（`src/assets_analysis.py`）

在 `src/assets_analysis.py::options_board()` 中调用 `src/wheel_scanner.py::scan_pool()`，把结果挂入 `board["wheel"]`：

- **位置**：`src/assets_analysis.py` 约第 2789 行 `options_board()`。
- **实现要点**：
  ```python
  from dataclasses import asdict
  from src.wheel_scanner import scan_pool

  def options_board() -> dict | None:
      """读取最新期权结构快照，附加每标的规则引擎结构解读与巨头池 Wheel 雷达。"""
      files = sorted((ROOT / "data" / "options_structure").glob("20*.json"))
      if not files:
          return None
      board = json.loads(files[-1].read_text(encoding="utf-8"))
      for s in board.get("symbols", {}).values():
          s["narrative"] = _options_narrative(s)
      try:
          snap_date, rows, summary = scan_pool()
          board["wheel"] = {
              "snapshot_date": snap_date,
              "summary": summary,
              "rows": [asdict(r) for r in rows],
          }
      except Exception as e:
          logger.warning("生成 wheel 雷达失败: %s", e)
          board["wheel"] = None
      return board
  ```
- **注意**：`GOOG` 与 `BRK.B` 在 `wheel_scanner.py` 的 `row["symbol"]` 是 `"GOOG"` / `"BRK.B"`，而期权页 `D.symbols` 的键是 `"GOOGL"` / `"BRK-B"`。可在 `ScanRow`（或挂载时）补一个 `opt_symbol: str` 字段（如 `spec.opt_symbol or spec.symbol`），方便前端点击标的直接调 `pick(row.opt_symbol)` 联动上方 Regime 大卡与下方 7 个图表 Tab。

---

### Step 2：前端期权页渲染与联动（`frontend/src/pages/assets/equities/options.astro`）

在 `frontend/src/pages/assets/equities/options.astro` 增加 **「确定性巨头池 · Wheel 雷达」** 小节（置于 `#board` 标的看板与 `#opt-tabs` 之间，或 `#sec-detail` 下方）：

1. **HTML 结构层（首个 `<script is:inline>` 之前）**：
   - 必须使用 `<Section>` 组件（遵守 `test_static_sections_use_section_component` 守卫，**禁止**手写裸 `<div class="re-section"><h2>`）：
     ```astro
     <Section id="sec-wheel" title="确定性巨头池 · Wheel 雷达" sub="35D IV / VRP · 52周 IVR · 趋势企稳与财报排雷" card>
       <span slot="head" id="wheel-regime-badge"></span>
       <div id="wheel-radar"></div>
     </Section>
     ```
   - `#page-toc` 锚点数组同步加入 `['Wheel 雷达', 'sec-wheel']`。
   - `load().catch(e => R.fail(['detail', 'board', 'wheel-radar'], e))`：确保 `R.fail` 里的 id 在页面真实存在。

2. **筛选控件与表格渲染（`renderWheel()`）**：
   - **梯队/信号筛选条（`.range-controls` + `.range-btn`）**：
     - 预设按钮：`全部 (17)` / `可出手信号`（含 `🟢 SELL PUT`、`🟡 跨财报·短Put`、`🔴 SELL CALL(持仓)`）/ `T0 核心池` / `T1 对冲池`。
   - **组合告警提示条**：若 `D.wheel.summary.warnings` 非空，在表头上方渲染紧凑提示条（如同板块超限告警、risk-off 提示）。
   - **主表格（复用 `R.table(headers, rows, formatters, keys, true)`）**：
     - 列设计：`['标的', '梯队', '现价 (单手)', 'RSI / MA50(σ)', 'HV20 / IV35 / VRP', '1Y IVR / IVP', 'Put / Call 墙', '事件排雷', '趋势', '当日信号', '推荐合约 (21~49D)']`
     - **标的列点击联动**：若 `D.symbols[row.opt_symbol]` 存在，渲染为 `<a href="javascript:pick('${R.esc(row.opt_symbol)}')" style="color:var(--accent-ink)">${R.esc(row.symbol)}</a>`，点击直接切换本页主视图标的。
     - **语义色硬约束（AGENTS.md §8）**：
       - 所有文字着色一律用**文字档 CSS 变量**（`var(--up)` / `var(--down)` / `var(--warning)` / `var(--accent-ink)` / `var(--text-dim)`），**禁止**在 `color:` 使用 `--color-up/down/warn` 图形档，**禁止**在 DOM `style=""` 中使用 `reCssVar()` 解析后的 hex 字面量。
       - 只读状态徽章（信号胶囊、梯队标签）一律走 `border-radius: 4px` 方胶囊类（如 `.opt-chip` 或 `.re-badge`），**禁止**在只读徽章上写 `999px` 圆角。
     - **推荐合约列**：
       - 仅当 `row.put_pick` 或 `row.call_pick` 存在时展示：`11-27(34d) $380P Δ-0.21 IV:37% (-7.8%)`；若 `executable === false` 追加弱化标记 `<em>[BSM估算]</em>`。
       - `⛔` 财报禁区与 `⚪` 不适格行后端已置 `put_pick = None`，前端显示 `—` 并附上 `row.reason`。
   - **卡片底部口径注脚**：
     - 调用 `R.secNote('口径：IV35 为期权链 ATM IV 曲线在 35 DTE 处线性插值；VRP = IV35 − HV20（≥+2% 才满足卖方正期望）；1Y IVR/IVP 来自 Barchart 52 周官方统计；财报 ≤10d 强制禁卖近月，11~35d 自动切换财报前到期合约。')` 挂在卡片底部。

3. **伴读抽屉上下文（`window.AICtx.declare('assets-equities-options', ...)`）**：
   - 在现有 `facts` 数组末尾追加 1~2 条 Wheel 雷达摘要事实：
     - `['巨头池 Wheel 信号', '🟢 ' + greenList + ' · 🟡 ' + watchList + ' · ⛔ ' + blockList, D.wheel.summary.market_regime]`
     - 若当前选中标的 `S` 在 `D.wheel.rows` 中，追加该标的的 `['当前标的 Wheel 评估', row.signal + ' · ' + row.trend_state, row.reason]`，让伴读 AI 能直接回答该票能否卖 Put/Call 及具体推荐合约。

---

### Step 3：静态导出与全套守卫验收

修改完成后，按顺序跑以下 3 条命令验收：

1. **后端与前端单源/对比度守卫测试**：
   ```bash
   uv run python -m pytest tests/test_wheel_scanner.py tests/test_assets_analysis.py tests/test_static_single_source.py tests/test_semantic_text_contrast.py -q
   ```
2. **重新导出静态 API JSON（更新 `frontend/public/api/assets/equities-options`）**：
   ```bash
   uv run python -m src.export_pages
   ```
3. **Astro 构建验证**：
   ```bash
   cd frontend && npm run build
   ```
4. **文档同步**：检查 `CONTEXT.md` 的「确定性巨头池（Wheel pool）」词条是否需要补充前端挂载位置说明。

---

## 3. 新 Session 启动提示词（直接复制给下一个 Session）

```text
请读取 docs/SPEC-wheel-web-integration.md，按里面的 Step 1 → Step 2 → Step 3 将 src/wheel_scanner.py 的扫描结果接入 /assets/equities/options.astro 页面并完成测试与构建验收。
```
