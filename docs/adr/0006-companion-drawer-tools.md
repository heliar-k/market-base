# ADR 0006 — 伴读抽屉的站内数据工具（浏览器执行 + 具名端点白名单）

**状态**：已接受
**日期**：2026-10-22

伴读抽屉的模型只看得见当前页面（`buildMessages()` 拼的页面坐标 / 事实集 / DOM 摘要 / 划词引用），
而站内有 239 个预渲染 `/api/*` 端点。模型对跨页、跨时点的读数一概答「页面未给出」。
决定给它一条**按需取数**的通道：把站内端点包装成 LLM function tools，由模型在回答中自主调用。

## 决策

**工具在浏览器执行，Worker 只放行不循环。**

- 工具执行器落在 `frontend/public/js/`（页面已有同源数据、已有 JS 运行时）。
- `functions/api/chat.js` 与 `src/chat_proxy.py` 的改动收敛为：校验层放行 `tools` / `tool_choice` /
  `tool` 角色消息，然后继续原样透传 SSE。**不在 Worker 里解析流、不写 loop。**
- 循环（累积 `tool_calls` 分片 → 执行 → 回填 → 续流）在 `ai-chat.js` 的 `runTurn` 里，
  上限 2 轮往返 / 3 次调用 / 8s 总耗时。

**工具是具名端点枚举，不是自由路径。**

每个工具绑定一个真实存在的端点，参数只做「下钻哪一层、取多少条」的收敛。不做
`fetch_url`、不做 `query_api(path)`——带自由路径参数的工具等于把站内端点暴露成扫描器。

**工具往返不落盘。** `thread.messages` schema 不因工具而变，`localStorage` / 导出 Markdown /
`/api/notes` 三个消费者零改动。工具结果有时效性，缓存旧结果比不缓存更糟。

（例外是思维链：T0 给 assistant 消息加了 `think` / `thinkMs` 两个字段用于回看，
那是思考模式的产物，不是工具往返的产物。但上游要求「带 `tools` 的请求必须回传
`reasoning_content`」，这两件事在 T1 会撞上，见 `docs/SPEC-ai-drawer-tools.md` 第 6 条。）

**失败结构化。** 静态站的端点可合法 404（`export_pages._safe()` 跳过当日缺数据端点），
执行器一律返回 `{error, endpoint, status}`，让模型能说「本期未导出」，不让整轮对话断掉。

## Considered Options

- **Worker 内跑循环**：Front 侧零改动，但要手写 SSE 解析、累积分片、再发第二轮，并受
  Pages Functions CPU 时间约束。数据本来就在同源，绕一圈没有收益。
- **档 0：服务端预检索**（Worker 判定后先搜一次，把结果拼进 system）：改动最小、延迟最低，
  但模型不能自主决定查什么、查几次；对「站内 239 个端点」这个场景召回精度全靠猜。
- **让模型自由传 URL / path**：实现最省事，但等于给模型一个站内扫描器，一次提示注入就能
  遍历全部端点。安全性不能靠提示词保证。
- **继续扩 DOM 摘要预算**：不引入新机制，但每轮都付全量 token 成本，且摘要永远覆盖不到
  用户没打开的那一页。
- **接 Exa / Tavily 做联网**：解决的是「页面外」的问题，和本 ADR 要解决的「页面间」不是一回事；
  且 DeepSeek 已于 2026-10-17 开放自研 Search/Fetch API（¥5/千次、同 key 同 base_url），
  真要联网优先用它，见 `docs/SPEC-ai-drawer-tools.md` 的非目标段。

## Consequences

- **服务端看不见工具调用**：Worker 只做透传，无法审计、无法按工具限流。缓解手段是前端
  的轮数/耗时上限，以及端点白名单本身（可查的集合在构建期就定了）。
- **白名单要跟数据走**：新增端点若想给模型用，得同步改工具定义里的 `enum`。这是有意的摩擦——
  端点暴露是需要决定的事，不该自动跟着 `export_pages.py` 膨胀。
- **提示词必须改口径**：现有「只依据当前页面…页面没给出的数字不要编造」与工具直接冲突，
  T5 要改成三层优先级（页面 → 工具 → 都没有）。
- **契约是两份实现的共同约束**：`tools` / `tool_choice` / `tool` 角色的校验规则要在
  `functions/_lib/common.js` 与 `src/chat_proxy.py` 同步，任一侧漏改即行为分叉。
- 领域词汇进 `CONTEXT.md`（伴读工具）。
