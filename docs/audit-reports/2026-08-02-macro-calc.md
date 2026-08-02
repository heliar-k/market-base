# 宏观计算模块审计报告

- 日期：2026-08-02
- 审计范围：宏观派生指标（`src/macro.py`）、利率预期（`src/fetchers/rate_expectations_fetcher.py`）、TUI/Server 标签展示（`src/server.py:464-468`）
- 方法：代码走读 + 公式独立抽查 + 全量测试复核 + validator 独立验证

---

## 一、总体结论

**无计算错误。** 主结论经两轮独立验证成立：

| 验证项 | 结果 |
|---|---|
| 公式一致性抽查 | 2s10s、NET_LIQUIDITY（RRP×1000）、SOFR-IORB 全部吻合 |
| 测试 | 全量 **231 passed**；宏观子集 99 passed（早前审计引用"84"为旧数字，实质无误） |
| ZQ→FOMC 概率链 | 代码走读正确（隐含利率 → 目标区间分配 → 概率） |
| validator 复核 | 4 项 minor findings **全部确认为真，0 误报** |

核心数值路径（宏观派生指标计算、GEX 相关公式、利率预期概率）可信，**不阻塞任何下游使用**。

---

## 二、发现清单（按优先级排序）

### P1-① server 年期限标签缺单位，且 replace 链为死代码

- **位置**：`src/server.py:464-468` `_label()`
- **问题**：
  1. 年期限不一致：`DGS10 → "10"`（server），而 TUI 显示 `"10y"` —— 同一条数据两个界面标签不同，用户易误读；
  2. `.replace("mo", "mo")` 字面 no-op；`.replace("MO", "mo")` 在 `.lower()` 之后永不触发（死代码）；
  3. 月标签 `1mo` 目前靠 `.lower()` 碰巧正确，属侥幸，脆弱。
- **影响**：仅展示层不一致，无计算影响。低危但用户可见。
- **建议**：删掉 replace 链，改用与 TUI 共用的映射表统一生成 label（如 `{"DGS10": "10y", "DGS1": "1m", "DFII10": "10y real", ...}`），两处引用同一常量，杜绝再分叉。**预估 0.5h。**

### P1-② `_current_target_range()` 硬编码兜底 3.50-3.75 静默

- **位置**：`src/fetchers/rate_expectations_fetcher.py:60-72`
- **问题**：FRED 本地数据缺失时静默回退到硬编码区间 `(3.50, 3.75)`，**无任何 warning 日志**。当前值恰为真实区间所以今天无害；一旦 FRED 断供 + 未来调息，输出将静默错位，且无痕迹可查。
- **影响**：潜伏性正确性风险（触发条件：数据缺失 × 区间变动）。今日无实际影响。
- **建议**：兜底分支加 `logger.warning("FRED 目标区间缺失，使用硬编码兜底 3.50-3.75")`。一行改动，让静默失效变显式。**预估 10min。**

### P2-① `_closest_range()` 死代码

- **位置**：`src/fetchers/rate_expectations_fetcher.py:79-83`
- **问题**：定义后 `src/` 与 `tests/` 零调用（validator 已核实）。
- **影响**：无功能影响，仅维护噪音（读者会误以为它是分配逻辑的一部分）。
- **建议**：直接删除。**预估 2min。**

### P3-① `days_after=3` 时 post_rate 对结算价噪声敏感（使用注意）

- **位置**：`rate_expectations_fetcher.py` 概率计算（FOMC 后第 3 日采样）
- **定性**：validator 判定为**方法论固有，非 bug**。post-meeting 隐含利率取自结算价，第 3 日仍受结算噪声影响，概率结果天然有抖动。
- **处理**：不作为缺陷修复；在函数 docstring 加一句使用注意（结果 ±5pp 内波动属正常）。**预估 5min。**

---

## 三、修复路线图

| 优先级 | 事项 | 工作量 | 价值 |
|---|---|---|---|
| P1-① | server/TUI label 统一映射，删 replace 链 | 0.5h | 消除展示不一致 + 死代码，防止回归 |
| P1-② | 兜底区间加 `logger.warning` | 10min | 静默失效 → 显式告警，成本极低 |
| P2-① | 删除 `_closest_range()` | 2min | 清理维护噪音 |
| P3-① | `days_after=3` 噪声说明写进 docstring | 5min | 避免误用 |

合计约 **1h**，全部为低风险小改动，可一次 PR 完成（P1 两项建议本轮做，P2/P3 顺带）。

## 四、结论

- **无阻塞项、无计算错误**，数据管道与 TUI 可继续正常使用；
- 4 项 minor 发现全部经 validator 确认真实（0 误报），其中 2 项值得本轮修复（P1-① 展示一致性、P1-② 静默兜底），2 项为顺手清理；
- 建议下个 commit 合入 P1 两项 + P2/P3 清理，随后关闭本次审计。

## 五、修复记录（2026-08-02，已合入）

| 项 | 修复方式 | 验证 |
|---|---|---|
| P1-① | `TERM_LABELS` 升级为 `config.TERM_INFO` 双字段表（长名+短标签），server/TUI 共用；server `_MACRO_LABELS` 删除 16 个期限条目，改由 `TERM_INFO` merge 注入 | 端点返回 `10y` 等带单位标签 |
| P1-② | 兜底区间常量 `config.FED_TARGET_RANGE_FALLBACK`，告警与返回值同源（修复后代码审查发现的双字面量漂移风险） | warning 触发验证 |
| P2-① | `_closest_range()` 删除 | grep 零引用 |
| P3-① | `fetch_rate_expectations()` docstring 加结算噪声说明 | — |
| 审查跟进 | 新增 `tests/test_config.py` 5 个不变量测试（TERM_INFO↔TERM_SERIES 双向覆盖、标签抽查、server merge 一致性、兜底区间网格合法性） | 236 passed |

后续待办（代码审查 G 级意见）：server `_MACRO_LABELS` 与前端 `static/js/macro-common.js` 的 `MACRO_LABELS` 仍各有一份约 60 条长名映射，为既有漂移面，建议另开任务统一。
