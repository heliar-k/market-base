# Spec：伴读抽屉站内数据工具（档 2）

## Problem Statement

伴读抽屉的模型只看得见「当前页面」：`ai-chat.js` 的 `buildMessages()` 每轮把页面坐标、
页面声明的事实集（2600 字）、DOM 摘要（1500/1200 字）和划词引用拼进请求，再没有别的通道。
提示词因此写成「只依据当前页面披露/估算的数据回答；页面没给出的数字不要编造」。

但站内有 **239 个预渲染的 `/api/*` 端点**（`src/export_pages.py` 产出）。问「上周 10Y 多少」
「和 CU 的相关性」「这个标的的期权墙」这类跨页、跨时点的读数，模型一个都摸不到，只能答
「页面未给出」——这正是用户觉得伴读「笨」的地方。

同一套页面里已经有这些数据，缺的只是一条让模型按需取数的通道。

## 目标 / 非目标

**目标**：让模型能在回答过程中主动查询站内端点，读到的数字与页面渲染的完全一致。

**非目标**：联网搜索（另案，见 ADR-0006 的 Considered Options）、写操作、跨站数据、
把现有页面事实集/DOM 摘要拆掉（它们仍然是零延迟的第一顺位信息源）。

## Implementation Decisions

### 1. 工具循环落在浏览器，不落在 Worker

`/api/chat` 现在是「一次请求 → 上游 SSE 原样透传」，Worker 完全不解析内容。加 tools 后若把
循环放进 Worker，就得在 Pages Functions 里手写 SSE 解析、累积 `tool_calls` 分片、再发起第二轮
请求，还要受 Worker CPU 时间约束。

浏览器侧执行反而更自然：数据在同源 `/api/*`、页面本来就有 JS、工具调用痕迹能直接渲染。
**Worker 的改动收缩为「校验层放行 + 继续透传」**，不引入任何循环逻辑。

代价（写进 ADR）：服务端看不见工具调用，无法审计或限流单个工具。

### 2. 工具目录是「具名端点枚举」，不是自由路径

不做 `fetch_any_url` / `query_api(path)` 这类带自由路径参数的工具——那等于给模型一个站内
扫描器，模型一旦被页面内容或工具结果带偏，就能遍历任意端点。

每个工具绑定**一个真实存在的端点**，参数里只有「取哪一层、要多少条」这类收敛选项。
T1 只发一个工具，枚举 3 个端点；T2 按族扩到 rates / credit / liquidity（约 20 个）。

### 3. 工具往返不落盘

`thread.messages` 保持现有 schema（`role` / `content` / `quotes` / 页面坐标 / `ts`），
**不存 `tool_calls`、不存 `tool` 角色消息**。三个消费者（localStorage、导出 Markdown、
`/api/notes` 落 KV）都按现状工作。

理由：工具结果有时效性，缓存旧结果比不缓存更糟；且导出的是给人读的研报，工具协议细节
只会污染版面。assistant 消息加一个轻量字段记录「本轮查了哪些端点」，仅供 UI 回显。

重试/编辑沿用现有语义：重发同一条提问，模型重新决定查什么，不复用旧工具结果。

### 4. 契约扩展（Worker 与 FastAPI 两份实现同改）

`docs/ai-chat-backend.md` 明写「两套实现共用同一份契约」，所以下面每一条都要在
`functions/_lib/common.js` 与 `src/chat_proxy.py` 同步落地。

**请求体新增（均可选）**：

| 字段 | 形状 | 服务端校验 |
|---|---|---|
| `tools` | `[{type:"function", function:{name, description, parameters}}]` | ≤8 个；`type` 只准 `function`；`name` 匹配 `^[a-zA-Z0-9_-]{1,64}$`；`description` ≤500 字；`parameters` 是对象。**不校验 JSON Schema 内容** |
| `tool_choice` | `"auto"` \| `"none"` | 只认这两个值；`required` / 具名函数在思考模式下会 400，直接不接受 |

**messages 角色扩展**：

- 新增 `tool` 角色，必须带 `tool_call_id`（≤128 字），缺了 → 422。
- `assistant` 消息可带 `tool_calls` 数组（≤8 条），原样透传。
- 40k 总字符上限仍按 `content` 累加，**`tool_calls` 里的 `arguments` 也计入**（否则可以绕过）。

**限额（两份实现取同一组数）**：

| 项 | 值 | 落点 |
|---|---|---|
| 单次工具结果 | ≤2000 字 | 前端执行器截断并标注（服务端不重复截） |
| 每轮工具调用 | ≤3 次 | 前端 `runTurn` |
| 往返轮数 | ≤2 轮 | 前端 `runTurn` |
| 工具总耗时 | 8s | 前端 `AbortController` |

### 5. 失败必须结构化，不能抛

静态站的端点可以合法 404（`export_pages._safe()` 会跳过当日缺数据的端点）。执行器把
所有失败收敛成一个 JSON 结果：`{error: "...", endpoint, status}`，让模型能说「这条数据
本期未导出」。抛异常会让整轮对话断掉——这是静态部署下最容易踩的坑。

### 6. `reasoning_content` 必须在带 tools 的请求里回传（上游硬约束）

官方思考模式文档：**携带 `tools` 参数的请求，在后续所有请求中必须完整回传
`reasoning_content`，即使该轮没实际调用工具；不回传直接 400。**

这条打在第 3 条决定上，需要额外说明：

- **单轮内**（工具往返的子请求）：无影响。`runTurn` 内存里本来就有完整思维链。
- **跨轮**（下一条 user 提问带历史）：受影响。thread 里落盘的 `think` 被 `THINK_MAX=6000`
  截断过，且历史 assistant 消息如果缺 `reasoning_content`，带 tools 的请求会 400。

T1 必须先解掉这一条，两种走法：

| 走法 | 代价 |
|---|---|
| **A. 带 tools 的请求只回传近期完整思维链**，早期答案不带 `think` 的退回纯文本（若上游仍 400，则整段历史剔除） | 多轮记忆质量下降 |
| **B. 落盘不截断**（`think` 全量进 localStorage/KV），构建消息时原样带上 | 存储膨胀，导出/存笔记体积变大 |

T1 开工第一件事是**用一个最小请求验证**：历史 assistant 消息缺 `reasoning_content` 时，
带 tools 的请求到底会不会 400。文档写得很硬，但这条要实测，别按文档猜——它决定 A/B 怎么选。

## 工具目录 v1（T1 只发这一个）

```json
{
  "type": "function",
  "function": {
    "name": "query_site_data",
    "description": "查询本站已导出的数据端点。用于页面摘要里没有的读数（其他页面、更长历史、其他标的）。返回 JSON 文本。",
    "parameters": {
      "type": "object",
      "properties": {
        "endpoint": {
          "type": "string",
          "enum": ["rates/yield-curve", "rates/real-rates", "volatility/dashboard"],
          "description": "要查询的端点"
        },
        "json_path": {
          "type": "string",
          "description": "可选的点号路径，如 data.current，只取该子树以节省上下文"
        }
      },
      "required": ["endpoint"]
    }
  }
}
```

执行器 `GET /api/{endpoint}`（静态站无 `.json` 后缀）→ 可选 `json_path` 下钻 →
`JSON.stringify` → 截断 2000 字。

## 验收标准

1. 在 `/rates/` 问「30Y 现在多少」（该数不在页面上）→ 模型调用 `query_site_data`，
   答案里的数字与 `/api/rates/yield-curve` 一致。
2. 页面已经给出答案时（如 `/rates/` 问 10Y）→ **不调用工具**，直接答。
3. 端点 404（模拟静态站缺数据）→ 模型说「本期未导出」，不得编造数字。
4. 断网 / 超时 → 同一句话术，对话不中断。
5. 工具往返不出现在 `localStorage` 的 thread 里，导出 Markdown 只有正文。
6. 两份代理实现（Pages / FastAPI）对同一份畸形请求（缺 `tool_call_id`、9 个工具、
   非法 `tool_choice`）返回同一批状态码。

## 切片

| 票 | 内容 | 依赖 |
|---|---|---|
| T0 | `deepseek-chat` → `deepseek-flash`；思考模式默认**开**，抽屉渲染可折叠思维链 | 无（**已完成**） |
| T1 | `query_site_data` 单工具 + 浏览器循环 + 契约放行 | T0 |
| T2 | 工具按族扩到 rates / credit / liquidity | T1 |
| T3 | 失败与降级（404 / 超时 / 截断 / 轮数上限） | T1 |
| T4 | 跨端点工具（相关性矩阵、期权墙） | T2 |
| T5 | 提示词口径改写 + 文档 + 测试收尾 | T1–T4 |

T1 是 tracer bullet：它证明了「模型发 `tool_calls` → 页面取数 → 回填 → 续流」这条链能通，
T2 之后全是往枚举里加值。**T1 通了但 T2 没做完，功能仍然可用**（一个端点也能答收益率问题）。
