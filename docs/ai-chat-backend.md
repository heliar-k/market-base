# 伴读抽屉后端通道（/api/chat · /api/models · /api/notes）

> 2026-10-22 落地。前端单源 `frontend/public/js/ai-chat.js`（配置纯逻辑在 `ai-config.js`）；
> 本文件只讲服务端通道与部署配置。领域术语见 `CONTEXT.md`。

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
  { model?: string, messages: [{role: "system"|"user"|"assistant", content: string}],
    stream?: true, reasoning_effort?: string }
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

## 前端怎么选通道

- **Base URL 保持默认（`https://api.openai.com/v1`）且没填 Key** → 走本站 `/api/chat`，
  模型由服务端白名单决定；设置面板禁用推理强度，并显示代理提示。
- **填了 Key，或 Base URL 改成别的** → 直连 `{Base URL}/chat/completions`（BYOK，行为不变）。
- 代理模式下「探测模型」请求 `/api/models`；直连模式仍请求 `{Base URL}/models`。

## 环境变量

本地 `.env`（`src/config.py` 启动时注入 `os.environ`）与 Cloudflare Pages 项目环境变量同名：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 空 | **必填**；为空时 `/api/chat`、`/api/models` 返回 503，前端提示填自己的 Key |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容根地址，代码自己拼 `/chat/completions` |
| `LLM_MODEL` | `deepseek-chat` | 默认模型，同时是白名单默认值 |
| `LLM_CHAT_MODELS` | 取 `LLM_MODEL` | 逗号分隔的代理模型白名单；客户端传别的会被覆盖成第一个 |
| `LLM_CHAT_MAX_TOKENS` | `1200` | 服务端封顶 64–8000，防止公开端点被刷长文 |
| `LLM_CHAT_ALLOW_EFFORT` | 关 | 打开后才把 `reasoning_effort` 透传给上游（默认丢弃，避免网关 400） |

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
  - chat：HTTP body ≤ 256 KB，messages ≤ 30 条，总字符 ≤ 40k，至少一条 `user`。
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

- `test_chat_backend.py`：FastAPI 路由（key 缺失 / 校验 / 白名单 / 限流 / 跨站与 body 上限 / 落盘去重）
- `test_pages_functions.py`：node 直接 import Pages Functions，假 fetch / 假 KV
- `test_ai_config.py`：代理模式判定、推理档位、重渲染草稿、effort 清理、缓存模式隔离、payload
- `scripts_dev/check_ai_chat_playwright.py`：浏览器侧验收（探测/换模型保留未保存字段、缓存切模式、
  effort 跨刷新、存笔记、答案显示实际模型）
