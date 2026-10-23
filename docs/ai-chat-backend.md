# 伴读抽屉后端通道（/api/chat · /api/models · /api/notes）

> 2026-10-22 落地。前端单源 `frontend/public/js/ai-chat.js`（配置纯逻辑在 `ai-config.js`）；
> 本文件讲服务端通道、部署配置，以及「模型到底看得见页面上的什么」。领域术语见 `CONTEXT.md`。

## 为什么有两套实现

| 运行形态 | 实现 | 能力 |
|---|---|---|
| 本地 `uv run python -m src.server` | FastAPI（`src/server.py`） | `/api/chat` 代理 + `/api/notes` 写 `data/notes/` |
| 线上 https://market-base.pages.dev | Cloudflare Pages Functions（仓库根 `functions/api/*.js`） | `/api/chat` 代理 + `/api/notes` 写 NOTES KV（未绑则 503） |

线上是纯静态 Pages，没有 Python 进程；`wrangler pages deploy frontend/dist` 会从**当前工作目录**
的 `functions/` 目录自动编译 Pages Functions（已在 wrangler v4 源码确认：`path.join(process.cwd(), "functions")`）。
`frontend/public/_routes.json` 只把 `/api/chat`、`/api/models`、`/api/notes` 三条路由交给 Function，
其余 `/api/*` 仍是 `export_pages` 预渲染的静态 JSON，不受影响。

两套实现共用同一份契约：

```
POST /api/chat
  { model?: string,
    messages: [{role: "system"|"user"|"assistant"|"tool", content: string | null,
                tool_calls?: object[],      # 仅 assistant：原样透传，≤8 条
                tool_call_id?: string}],    # 仅 tool 角色必填，≤128 字
    stream?: true, reasoning_effort?: string,
    tools?: [{type: "function", function: {name, description, parameters}}],  # ≤8 个
    tool_choice?: "auto" | "none" }
  → 200 text/event-stream（上游 SSE 原样透传；响应头 X-LLM-Model = 实际使用的模型）
  → 4xx/5xx {error: "..."}（前端 apiError 直接显示这句话）

GET /api/models
  → { data: [{id, display_name}], proxy: true }
  → 503 {error: "本站代理未配置 LLM_API_KEY..."}

POST /api/notes
  { filename?: string, markdown: string, thread?: object }
  → { ok: true, path: "data/notes/..." }            # 本地
  → { ok: true, key: "notes/YYYY-MM-DD/...", storage: "kv" }  # 线上绑了 NOTES
  → 503 {error: "静态部署没有 data/ 写权限..."}
```

## 工具通道（tools / tool_choice / tool 角色）

> 2026-10-22（T1）。浏览器侧工具循环的前置条件，见 ADR-0006。
> **服务端不在 Worker 里跑循环**：只放行字段、继续原样透传 SSE，循环在 `ai-chat.js` 的 `runTurn`。

两份实现同一套规则：`functions/_lib/common.js` 的 `validateTools` / `validateToolChoice` /
`validateMessages` 与 `src/chat_proxy.py` 的同名函数逐条对齐；`tests/test_pages_functions.py`
对**同一批**畸形请求断言与 `tests/test_chat_backend.py` 相同的状态码（`docs/SPEC-ai-drawer-tools.md`
验收标准第 10 条）。

**请求体新增（均可选）**：

| 字段 | 形状 | 校验 | 违规 |
|---|---|---|---|
| `tools` | `[{type:"function", function:{name, description, parameters}}]` | 数组 ≤8 个；每项四个字段都必填：`type` 只准 `"function"`、`name` 匹配 `^[a-zA-Z0-9_-]{1,64}$`、`description` 是字符串且 ≤500 字、`parameters` 是对象（**不校验 JSON Schema 内容**） | 422 |
| `tool_choice` | `"auto"` \| `"none"` | 只认这两个值（大小写敏感） | 422 |

`tool_choice: "required"` 与具名函数对象在思考模式下会被上游直接 400（实测原文
`雷火网关: Thinking mode does not support this tool_choice`），所以入口就拒掉，不把错误留给上游。
空 `tools: []` 视同未传（不下发），避免上游对空数组的行为不确定。

**messages 扩展**：

| 角色 | 新字段 | 规则 | 违规 |
|---|---|---|---|
| `tool`（新增） | `tool_call_id` | **必填**，非空字符串，≤128 字 | 422 |
| `assistant` | `tool_calls` | 可选，数组 ≤8 条，每项是对象，`function.arguments` 是字符串；**原样透传**（不重排、不裁字段） | 422 |
| 任意 | `content` | 允许 `null` / 缺失（assistant 只带工具调用时上游就发 null），校验层归一成 `""` | 非字符串且非 null → 422 |

两侧校验层都会重建 message 对象（原先只保留 `role` / `content`），**最容易漏的就是
`tool_call_id` / `tool_calls`**——不显式带上，工具往返会被抹成两条无关联的普通消息。

**40k 总字符预算**仍按 `content` 累加，另加 `tool_calls[].function.arguments` 的字符数——
否则工具参数可以绕过预算。两侧都有同一条跨线用例：`'hi'` + 39998 过、+ 39999 → 413。

**工具限额**（两份实现取同一组数，但**这几项落在前端执行器**，服务端不重复校验）：

| 项 | 值 | 落点 |
|---|---|---|
| 单次工具结果 | ≤2000 字 | 前端执行器截断并标注 |
| 每轮工具调用 | ≤3 次 | 前端 `runTurn` |
| 往返轮数 | ≤2 轮 | 前端 `runTurn` |
| 工具总耗时 | 8s | 前端 `AbortController` |

服务端只校验「单条请求长什么样」，不管「一轮里调了几个工具、跑了几轮」——它看不见循环
（ADR-0006 的 Consequences）。工具失败也由前端收敛成 `{error, endpoint, status}`，服务端不参与。

**工具的目录与契约见 `docs/SPEC-ai-drawer-tools.md`；服务端只知道 `tools` / `tool_choice` / `tool`
角色，不知道有哪几个工具。** 加工具改的是前端的工具目录、提示词索引与执行器，本节与两份代理
实现都不用动——除非新工具需要契约里没有的字段。


## 前端怎么选通道

- **Base URL 保持默认（`https://api.openai.com/v1`）且没填 Key** → 走本站 `/api/chat`，
  模型由服务端白名单决定；设置面板禁用推理强度，并显示代理提示。
- **填了 Key，或 Base URL 改成别的** → 直连 `{Base URL}/chat/completions`（BYOK，行为不变）。
- 代理模式下「探测模型」请求 `/api/models`；直连模式仍请求 `{Base URL}/models`。

## 页面上下文（模型到底看得见什么）

三层进 system message、一层进 user message（`ai-chat.js` 的 `buildMessages`）：

| 层 | 落到哪条消息 | 内容 | 预算 | 单源 |
|---|---|---|---|---|
| 页面坐标 | system | 标题 / 路径 / 时效（页头 `re-as-of`，SPA 用「标的 · 最新价」顶替） | — | `getPageContext()` + `AI_CONTEXT_LABELS` |
| 页面事实集 | system | 页面自己声明的结构化读数、关系矩阵 | 2600 字 | `js/ai-context.js`（页面 `AICtx.declare`） |
| DOM 摘要 | system | 结论行 / KPI 卡 / 键值行 / 结构洞察 / 小节标题 | 1500 字；有事实集时 1200 字 | `pageSummary()` |
| 划词引用 | **user**（每条消息前缀） | 选中片段 + 就近小节标题 + 表格表头与本行 | 2–2000 字/条 | `ai-prompt.js` 的 `formatQuote` |

三条硬约束：

1. **DOM 摘要只收可见元素**（`offsetWidth/Height/getClientRects` 全零即跳过）。SPA 是双视图、
   切换只改 `.app[data-view]`，隐藏视图的文字仍在 DOM 里——不加这条，关联视图会把技术视图的
   1119 字读数（RSI / MACD）当成页面上下文送进模型，模型拿它编相关性。
2. **canvas 上的数据必须由页面显式声明**。相关性热力图、K 线、GEX 曲面在 DOM 里一个数都没有；
   只抓 DOM 时模型看得见「结构洞察」那几句话，任意两标的的系数一概查不到。
3. **明细表这一类暂不迁**（本轮只做标量读数与关系矩阵）：拍卖明细 106 行、持仓表这类
   整表塞不进预算，模型对表内具体行的了解仍靠划词——引用块会带表头与本行
   （`formatQuote`），不划词就只有该页声明的那几条汇总读数。

给新页接入：

```js
// 数据到手之后（id 幂等，重复 declare 即覆盖）。守卫可省——本脚本与页面脚本同为
// 同步经典脚本且排在前面，但线上 CDN 半失败时少写一行就少一次白屏机会
if (window.AICtx) AICtx.declare('rates', () => ({
  title: '利率曲线读数',
  src: 'FRED（DGS / EFFR / TIPS）',
  asOf: d.as_of,
  // 数值格式化一律复用 rates-common 的 R.fmtPct / R.fmtBp / R.num，页面不自写 toFixed
  facts: [['10Y', R.fmtPct(t['10Y'].current), '1周 ' + R.fmtBp(t['10Y'].chg_1w) + 'bp']],
  matrix: AICtx.matrix(labels, square), // 可选：交方阵，下三角由 ai-context 转
}));
```

矩阵交**方阵**给 `AICtx.matrix(labels, square)`，由它转下三角（第 i 行取 `square[i][0..i]`）：
22 标的编码后约 2.1k 字，整张 22×22 塞得进一档预算。预算不够时**整块丢矩阵**而不是截一半
（半张表会让模型查到错位的对），并留一行「关系矩阵超出上下文预算，已省略」；
连读数都放不下时逐条截断并报省略条数，整块都放不下的块合并成一行「另有 N 个数据块……已省略」。

分界：**块长什么样、怎么编码、预算怎么裁**只在 `js/ai-context.js`（`render()` 的默认预算也在那里，
`ai-chat.js` 不传参）；**这句话怎么对模型说**只在 `ai-prompt.js`（`AI_SYSTEM_PROMPT` /
`AI_FACTS_HINT` / `AI_CONTEXT_LABELS`）；**每条读数取哪个字段**由页面自己写。
页面若要用「现在是否看得见」决定送不送（SPA 切视图、同页子模式），用 `AICtx.isVisible(el)`
而不是自己判 `data-view`：关联页的「宏观联动」子模式就是这么把 `#corr-asset-wrap` 置
`display:none` 的，只看 `data-view` 会把看不见的矩阵继续送出去。
已接入：相关性（SPA）、`/rates/`、`/treasury/`。


## 环境变量

本地 `.env`（`src/config.py` 启动时注入 `os.environ`）与 Cloudflare Pages 项目环境变量同名：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 空 | **必填**；为空时 `/api/chat`、`/api/models` 返回 503，前端提示填自己的 Key |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容根地址，代码自己拼 `/chat/completions` |
| `LLM_MODEL` | `deepseek-flash` | 默认模型，同时是白名单默认值（`deepseek-chat` 已于 2026-07-24 停用） |
| `LLM_CHAT_MODELS` | 取 `LLM_MODEL` | 逗号分隔的代理模型白名单；客户端传别的会被覆盖成第一个 |
| `LLM_CHAT_MAX_TOKENS` | 思考开 `4000` / 关 `1200` | 服务端封顶 64–8000。思考 token 也计入 `max_tokens`，所以开着思考时必须抬高，否则正文被思维链吃光 |
| `LLM_CHAT_ALLOW_EFFORT` | 关 | 打开后才把 `reasoning_effort` 透传给上游（默认丢弃，避免网关 400） |
| `LLM_THINKING` | **开** | DeepSeek 地址上显式发 `thinking:{type:"enabled"}`（不依赖上游默认值）；设 `off` 关闭。`src/llm.py` 的叙事改写共用同一开关。非 DeepSeek 地址永不下发该字段 |

工具通道**不引入新环境变量**（工具目录在前端，`/api/chat` 只透传）。要留意的是
`LLM_THINKING` 与 `tool_choice` 的相互作用：思考模式下上游只接受 `auto` / `none`，
`required` / 具名函数会 400，所以入口层就直接 422（见上节）。

Cloudflare 侧一次性设置（Base/Model 用默认就不用额外设）：

```bash
npx wrangler pages secret put LLM_API_KEY --project-name=market-base
# 要改 Base URL / 模型：
# Pages 项目 → Settings → Environment variables → Production/Preview 添加
#   LLM_BASE_URL / LLM_MODEL / LLM_CHAT_MODELS / LLM_CHAT_MAX_TOKENS
```

注意：`.github/workflows/deploy-pages.yml` 的 `Sync Pages LLM secret` 步骤会用同一个
GitHub secret `LLM_API_KEY` 调 `wrangler pages secret put` 同步到 Pages Functions 运行时
（secret 为空则该步跳过，页面照常部署，代理返回 503）。也可以手动跑一次上面的命令，或在
Pages 面板里设。

## 保护线

- 限流（本地进程 / 每个 isolate 内，best-effort）：chat 10 次/分/IP，models 30 次/分/IP，
  notes 20 次/小时/IP；超了 429 + `Retry-After`。数值三处同步：`src/server.py` 的
  `_LIMITS`、`functions/_lib/common.js` 的 `RATE_LIMITS`、本表。
- 输入上限：
  - chat：HTTP body ≤ 256 KB，messages ≤ 30 条，总字符 ≤ 40k（**含 `tool_calls[].function.arguments`**），
    至少一条 `user`。工具字段：`tools` ≤ 8 个、`tool_calls` ≤ 8 条、`tool_call_id` ≤ 128 字（见上节）。
    256 KB 是按字节留的余量：40k 中文字符 ≈120 KB UTF-8（旧的 64 KB 上限会误杀合法输入）。
  - notes：markdown ≤ 1 MB **字节**，JSON body ≤ 4 MB（含 thread/转义）。
  - 本地 FastAPI 用 `Content-Length` 拦超限并拒绝 chunked（411）；Pages 侧读完后按
    `TextEncoder` 字节数复核，两种写法都不会因中文被提前 413。
- 输出上限：`max_tokens` 默认 1200（环境变量可调，最多 8000）。
- 模型白名单：客户端只能选 `LLM_CHAT_MODELS` 里的；不在白名单 → 用第一个。
- Key 只在服务端：代理模式前端不带 `Authorization`，浏览器永远拿不到服务端 Key。
- 本地新端点额外做 Origin 白名单（只允许 localhost/127.0.0.1/::1）：全局 CORS 仍是 `*`，
  但 `/api/chat`、`/api/notes` 不接受外站页面调用，避免任意网页写 `data/notes/` 或烧 Key。
- 公网写入口（notes）只有绑了 KV 才可用，且文件名消毒、限流、单文件 ≤ 1 MB。

## /api/notes 落哪

- **本地**：`data/notes/<filename>.md`；同名自动追加 `-2`、`-3`；`../` 之类路径会被
  `Path(...).name` 剥掉，不能穿越。`data/notes/` 已在 `.gitignore`（个人笔记不进公开仓）。
- **线上**：绑定 KV `NOTES` 时写 `notes/YYYY-MM-DD/<timestamp>-<name>.md`；
  没绑返回 503，提示回本地跑服务或点「导出 .md」。
- 可选 KV：

  ```bash
  npx wrangler kv namespace create NOTES
  # 把输出的 id 配到 Pages 项目 → Settings → Functions → KV namespace bindings
  # 变量名必须是 NOTES（functions/api/notes.js 读 env.NOTES）
  ```

## 本地验证

```bash
# 1) 代理通道：.env 里加 LLM_API_KEY（和 GitHub secret 同一个）
#    注意用 :8000（FastAPI）打开页面才有同源 /api/chat；:4321 是 Astro 纯静态预览，没有后端
uv run python -m src.server
curl -s localhost:8000/api/models
curl -N -X POST localhost:8000/api/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"用一句话解释 2s10s"}]}'

# 2) 笔记落盘
curl -s -X POST localhost:8000/api/notes \
  -H 'content-type: application/json' \
  -d '{"filename":"demo.md","markdown":"# hi"}'
ls data/notes/

# 3) Pages Functions（不登录也能本地跑）
cd frontend && npm run build && cd ..
npx wrangler pages dev frontend/dist --binding LLM_API_KEY=sk-test
```

## 测试

```bash
uv run python -m pytest tests/test_chat_backend.py tests/test_pages_functions.py tests/test_ai_config.py -q

# 浏览器验收（探测保留字段 / 缓存按模式隔离 / effort 清理 / 存笔记 / 模型口径）
# 先起一个能打开 /rates/ 的站点；脚本会 mock /api/*，不连网
uv run python scripts_dev/check_ai_chat_playwright.py http://127.0.0.1:8000
```

- `test_chat_backend.py`：FastAPI 路由（key 缺失 / 校验 / 白名单 / 限流 / 跨站与 body 上限 / 落盘去重）；
  工具契约（9 个 tools → 422、非法 `tool_choice` → 422、`tool` 缺 `tool_call_id` → 422、
  `arguments` 计入 40k → 413、合法 tools 透传到上游 payload）
- `test_pages_functions.py`：node 直接 import Pages Functions，假 fetch / 假 KV；工具契约与
  Python 侧**同一批**畸形请求，断言状态码一一对应
- `test_ai_config.py`：代理模式判定、推理档位、重渲染草稿、effort 清理、缓存模式隔离、payload
- `scripts_dev/check_ai_chat_playwright.py`：浏览器侧验收（探测/换模型保留未保存字段、缓存切模式、
  effort 跨刷新、存笔记、答案显示实际模型）
