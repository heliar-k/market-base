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
| 工具总耗时 | 8s | 前端批次 `AbortController`（硬上限） |
| 单次取数 | 3.5s | 前端每次尝试独立 `AbortController`；重试仍要落在 8s 以内 |

**预算用尽必须强制开口**（2026-10-22 T1 实测补的规则）。模型很容易把两次往返预算全花在
「钻 `json_path`」上，每轮 `finish_reason` 都是 `tool_calls`；若到上限直接 `break`，
正文是空的，用户只会看到「（接口没有返回内容）」。

所以：**往返用尽而模型仍在要工具时，再发一次请求并带 `tool_choice: "none"`**，把工具定义
留着、只禁调用，逼它用已查到的信息作答；这一轮再收到 `tool_calls` 就直接结束，不再续转。
最坏结果是「未导出」而不是空白。`tool_choice` 因此必须按轮可覆盖（`chatPayload` 的第 6 参）。

**大结果要给「可下钻的结构摘要」，不是前 2000 字**。`rates/real-rates` 是 88KB（7 条 × 365 点），
硬截只能得到断在数字中间的半截 JSON。执行器的策略是：≤2000 字原样返回；超了且没给
`json_path` → 返回顶层结构摘要（键名 / 类型 / 数组长度 / 短标量，对象数组连首元素键名一起给）
并提示可下钻的示例路径；给了 `json_path` 且下钻结果是长数组 → 首尾各 3 条 + 总长度。

### 5. 失败必须结构化，不能抛

静态站的端点可以合法 404（`export_pages._safe()` 会跳过当日缺数据的端点）。执行器把
所有失败收敛成一个 JSON 结果：`{error: "...", endpoint, status}`，让模型能说「这条数据
本期未导出」。抛异常会让整轮对话断掉——这是静态部署下最容易踩的坑。

#### 降级的三条落地（T3，2026-10-22）

**① 单次取数 3.5s，与批次 8s 分工。** 起因是一次实测：`GET /api/rates/yield-curve` 卡了
`8002ms` 才被批次 controller 掐掉——浏览器传输层根本没把请求发出去（服务端日志里没有这条
GET），而重试门槛要求「剩余 ≥2s」，一个卡死就把整轮预算吃光，最后只能答「未导出」。

现在每次尝试自建 `AbortController`，上限 `min(剩余总预算, 3.5s)`，并与批次 8s、用户停止
两个信号联动；三种中断的文案可区分（`单次 3.5s` / `本轮 8s 预算已用尽` / `已取消（用户停止）`）。
3.5s 超时后通常还剩 ~4.5s，重试规则仍能再试一次；两次都超时才落错误（≈7s，不破 8s）。

**② 同轮端点缓存，按 `endpoint + json_path` 归并。** 每轮只有 3 次调用预算，而模型下钻时
完全可能把同一端点问两遍。缓存里存的是**取数的 promise**（不是结果）：miss 时先
`cache.set(key, promise)` 再 await，同一批 `Promise.all` 里后到的重复调用拿到的是在途
promise，不会两条都 miss 再各发一次 GET——这条是验收脚本抓出来的真 bug（初版
`get → fetch → set` 在并发下等于没缓存）。失败也缓存（同轮同键的 404 不会再烧一次预算），
`tool_call_id` 永远用本次调用自己的 id。缓存 Map 在 `runTurn` 内新建，跨轮绝不复用。

**③ 失败要在 UI 上看得出来。** `msg.tools` 保持字符串数组（只放成功端点，旧 thread 兼容），
新增 `msg.toolsFailed`；meta 渲染成 `查数 A / B · 未取到 C`。同一个端点本轮既成功又失败时
按成功算（不同 `json_path` 才会这样），避免出现「查数 A · 未取到 A」这种自相矛盾的行。

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

### 实测结论（2026-10-22，已验）

跑 `uv run python scripts_dev/check_tools_upstream.py`，六项全 PASS：

| 项 | 结果 |
|---|---|
| R2 思考模式回 `reasoning_content` | 是（84 字） |
| R3 带 tools 触发 `tool_calls` | 是，`finish_reason: tool_calls` |
| R4 流式 `arguments` 分片拼接 | 26 个分片拼回合法 JSON |
| R5 回填 `tool` 结果后续流 | 正文引用了回填的 4.812 |
| **R6 历史 assistant 缺 `reasoning_content` + tools** | **200，不强制** |

R6 还测了更严的形状（assistant 带 `tool_calls` 但无 `reasoning_content` + `tool` 回填），同样 200。

**结论：A / B 都不用做。** thread schema 维持现状，`buildMessages()` 不额外回传
`reasoning_content`（每轮带上 4000 字的思维链只会挤爆 40k 字符预算），
`think` 继续是「显示用的截断值」。

**为什么这个 200 可信**：同一条链上 `tool_choice: "required"` 在思考模式下确实 400
（`雷火网关: Thinking mode does not support this tool_choice`）—— 说明上游错误是原样透传的，
不是网关把 messages 规范化后吞掉，R6 的 200 不是假阴性。

**残余风险与防线**：实测打的是本地网关（magpie → 雷火 → deepseek 兼容链），
而生产默认 base 是 `https://api.deepseek.com`（`deploy-pages.yml` 只同步 key、不设 base）。
官方若更严格，症状是「带 tools 的第二轮及以后 400」。防线只写一条、十行以内：
`runTurn` 捕获「400 且响应体提到 `reasoning_content`」→ 丢掉历史里除最后一条 user 之外的消息
重发一次（**保留 tools**）。只降级该轮记忆，不丢查数能力。

**重跑时机**：换 `LLM_BASE_URL` / 换 key / 网关改版后重跑该脚本，别继承旧结论。

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
序列化时按第 4 条走体积策略（≤2000 字原样返回，超了给结构摘要或首尾采样，不硬截半截 JSON）。

## 工具目录 v2（T2）

17 个端点 / 4 族（v1 的 3 个 + 新增 14 个）。目录单源是 `TOOL_CATALOG`
（`frontend/public/js/ai-tools.js`：`path` / `group` / `label`），`enum` 与提示词里的端点索引都从它派生；
下面这张表是那张表的审计依据，逐个数的是 `frontend/public/api/` 里 2026-10-19 的真实产物。

> 「下钻后」的字数不是估的：把真实 JSON 喂给执行器的 `jsonPath()` + `renderToolResult()`
> 跑一遍，得到的就是**模型实际收到的那段文本的字符数**（含 `_note`）。全部 ≤2000 字上限。

| 端点 | 分组 | 大小 | 顶层键 | 能回答什么 | 推荐下钻路径（下钻后字数） |
|---|---|---|---|---|---|
| `rates/yield-curve` | rates | 16.9KB | `generator` · `yield_curve`（14 子键） | 曲线形态与驱动、2s10s/3m10s/5s30s 及 1 个月变化、11 个期限收益率、5 个期限的名义-实际-盈亏平衡、132 日利差历史、中美日长端对照 | `yield_curve.spreads`（82，四组利差 + 1m 变化）/ `yield_curve.tenors`（1207，11 行含 1w/1m/3m 变化）/ `yield_curve.spreads_history.2s10s`（295，首尾采样） |
| `rates/real-rates` | rates | 86.4KB | `as_of` · `nominal_10y` · `real_10y` · `breakeven_10y` · `term_premium` · `term_premium_chg_1m` · `y2` · `spread_2s10` | 10Y 名义/实际/盈亏平衡/期限溢价的当前值与一年路径、2s10s | **顶层没有标量当前值**（8 个键里 6 个是 365 点数组）：`term_premium_chg_1m`（3）是唯一数值标量；当前读数钻 `real_10y` / `nominal_10y`（305 / 307，`last` 末条即最新）/ `spread_2s10`（379，单位 %，末条 0.33 = 33bp） |
| `rates/fed-funds` | rates | 68.7KB | `as_of` · `latest` · `corridor` · `sofr_vol` · `effr_history` · `recent` | EFFR、SOFR、SOFR 成交量、目标区间、政策走廊 7 条 90 日序列（EFFR/SOFR/TGCR/BGCR/ON RRP/目标区间上下限）、1250 日 EFFR 历史 | `latest`（188，6 个当前读数）/ `latest.effr`（35）/ `recent`（1021，30 天）；别直接钻 `effr_history`（1250 点只剩首尾） |
| `rates/auctions` | rates | 38.9KB | `as_of` · `avg_cover_10_coupon` · `upcoming_count` · `recent` · `upcoming` · `trend` · `tail_trend` · `indirect_trend` · `bill_share` · `bill_share_latest` · `borrowing` | 近 10 场名义付息券平均认购倍数、未来 16 场日历、四期限 365 天需求趋势、Bill 占比、QRA 借款估算 | 按期限一律 `trend.10Y`（748，认购倍数 12 行）/ `tail_trend.10Y`（735）/ `indirect_trend.10Y`（743）；`bill_share_latest`（5）/ `borrowing`（100）；**别用 `recent`**（106 行混 Bill/Note/Bond，采样只给最新 3 条 Bill） |
| `rates/analysis` | rates | 4.5KB | `generator` · `overview` · `yield_curve` | 规则引擎的四段研判（曲线形态 / 实际利率 / 联储预期 / 展望）+ `overview.effr` | `overview`（669，四段正文 + effr）/ `overview.sections`（581，只要散文）/ `yield_curve.spreads`（82，与 yield-curve 同值） |
| `credit/overview` | credit | 4.5KB | `regime` · `ig` · `hy` · `hy_ig` · `layers` · `spreads` · `funding` · `sloos` · `quality` · `fincond` · `liq_etf` · `signals` · `generator` · `as_of`（14 键） | IG/HY OAS 与分位、6 层利差（IG→CCC）、CCC−BB / BBB−IG、企业与银行融资成本、SLOOS、逾期率与核销率、NFCI 家族、三段 where/what/watch | `regime`（475，61.0 + 7 分量）/ `hy`（114）/ `spreads`（373）/ `signals`（316） |
| `credit/stress` | credit | 9.7KB | `composite` · `zone` · `components` · `cross` · `history` | 0-100 压力指数（44.7/中性）、5 分量与权重、与 VIX/MOVE/DXY/10Y 的横向偏离、500 日历史 | **整包摘要即答案**（`composite`/`zone` 是顶层标量）；历史钻 `history.values`（127，首尾采样，末条=最新） |
| `credit/cds` | credit | 1.0KB | `sovereign` · `bank` · `generator` · `as_of` | 主权 CDS 代理 510bp、银行 CDS 与 SPX 的 14 日背离（−1.8pp）、30 日两只走势 | **整包**（1034，不用 json_path）；只要背离 `bank`（925） |
| `liquidity/snapshot` | liquidity | 1.5KB | `cards` · `evaluation` · `data_date` | 四个余额卡（总资产 / RRP / TGA / 净流动性，含 1m/1y 变化）+ 三段研判 | **整包**（1081）；单卡 `cards.NET_LIQUIDITY`（95） |
| `liquidity/reserves` | liquidity | 5.5KB | `cards` · `spreads` · `rows` · `series` · `judgement` | 准备金余额（4 周变化）、SOFR−3M T-Bill 与 SOFR−IORB、20 天余额、60 日利差序列 | `cards`（159）/ `spreads`（41，两个标量）/ `rows`（861，20 天） |
| `liquidity/rrp-tga` | liquidity | 11.4KB | `alert` · `calendar` · `current` · `cashflows` · `data_date` · `series` · `judgement` | RRP / TGA / 净流动性当前值、未来 21 天财政日历（7d/14d 净抽水）、SOFR−IORB 档位、60 日现金流 | `current`（80）/ `alert`（69）/ `calendar.net_7d_b`（5）/ `judgement`（119） |
| `liquidity/fed-balance-sheet` | liquidity | 3.1KB | `cards` · `rows` · `judgement` · `note` | 总资产 / 国债 / MBS / 净流动性（万亿）、20 行日轴（周度发布，非发布日为 null，附 SPX/NDX）、缩表研判 | `cards`（190，最新值）/ `rows`（862，20 行）；**别读 `rows` 末行**（Fed 字段为 null） |
| `liquidity/operations` | liquidity | 9.6KB | `rmp` · `recent` · `soma` · `srf` · `as_of` · `rmp_series` · `judgement` | RMP 累计投放与月均（vs 目标）、SOMA 三项周/月变化、SRF 90 天激活天数、40 笔操作明细 | `rmp`（85）/ `soma`（175）/ `srf`（34）/ `recent`（971） |
| `liquidity/subsurface` | liquidity | 2.9KB | `percentiles` · `volume` · `srf` · `swap` · `composite` · `as_of` · `volume_series` · `judgement` | 次表层压力 6.0/10（承压）、SOFR 五档分位 vs IORB、成交量 z 值、SRF 与互换余额 | `composite`（104，四项分 + state）/ `percentiles`（81）/ `judgement`（150） |
| `liquidity/transmission-chain` | liquidity | 3.1KB | `score` · `score_raw` · `band` · `pctile_30d` · `history` · `layers` · `confirmations` · `offshore` · `external` · `evidence` · `data_date` | LPI 4.7/中性（30 日 73 分位）、三层加权拆解、6 条确认项、5 个货币基差、证据面板 | **整包摘要即答案**（`score`/`band`/`pctile_30d` 是顶层标量）；拆解钻 `layers`（626）/ `confirmations`（394）/ `evidence`（418） |
| `liquidity/global-dollar` | liquidity | 0.5KB | `dxy` · `swap` · `pairs` · `score` | DXY 与日变动、央行互换余额（30 日变化）、5 个货币对 1W–1Y 基差、全球美元指数 | **整包**（474）；单看 `dxy`（50）/ `pairs`（334） |
| `volatility/dashboard` | volatility | 11.1KB | `generator` · `as_of` · `as_of_snapshot` · `hero` · `signals` · `indices` · `stats` · `risk_matrix` · `trade_map` · `narrative` · `term_structure` · `zones` | VIX / VVIX / MOVE / OVX、30 个波动率指数涨跌、6 组风险矩阵、期限结构与升贴水、交易地图、六段叙事 | `hero`（301）/ `term_structure`（160）/ `risk_matrix`（1033）/ `indices`（973，30 行首尾采样）；`narrative`（1519）是规则引擎散文 |

**这张表读出来的三件事**：

1. **17/17 都能「一次 `json_path`」落到能回答问题的层**——12 个需要一次下钻，2 个首跳摘要里
   已有主读数（`credit/stress` 的 `composite`、`transmission-chain` 的 `score`），3 个整包就
   ≤2000 字（`credit/cds`、`liquidity/snapshot`、`liquidity/global-dollar`）。
2. **路径猜错不是多花一跳，是烧掉 1/3 的每轮调用预算**：`json_path` 打偏时执行器返回
   `{error:"JSON 路径不存在：…", available:[该层可用键]}`，这次调用算数、但一个数据都没拿到
   （每轮 3 次调用 / 2 轮往返，见第 4 条）。所以「端点名 → 含义」的索引还不够，**下钻路径的
   可猜性**才是这张表要保的东西：顶层键名自己得说得清（`current` / `latest` / `cards` / `spreads`），
   别让模型在两跳之外才见到主读数。
3. 真正吃紧的不是深度而是**宽度**：`rates/auctions.recent`（106 行）、`credit/stress.history`
   （500 点）、`volatility/dashboard.indices`（30 行）这类长数组，采样只剩首尾 3 条；
   缺的是「按 key 过滤」（如只取 `security_term` 命中的行），T2 不做，改成在同一包里预先
   聚合成按期限分桶的 `trend.*`（auctions 已经这么做了）。

### 选型：为什么不收 `liquidity/overview_*`

`export_pages.py` 会为 liquidity 的 10 个区间（1m/3m/6m/1y/2y/3y/5y/10y/30y/all）各导一份
`api/liquidity/overview_{range}.json`（**这组带 `.json` 后缀**，与目录里其他端点不同）。
10 个文件合计 5.2MB，最大 `overview_all` 1.9MB、`overview_30y` 1.5MB。

拆 `overview_all` 看：`summary` 0.4KB + `series` 1276KB + `stacked` 649KB —— **99.97% 是图表点对**。
而 `summary` 里那 5 个 `latest_value` 已被 `liquidity/snapshot.cards`（WALCL / RRP / TGA /
净流动性）与 `liquidity/reserves.cards`（准备金）覆盖，没有独占读数。模型真要时间序列，
钻具体端点里的短序列更划算：`reserves.rows` 20 点 861 字、`rrp-tga.series.tga_b` 60 点 205 字、
`fed-balance-sheet.rows` 20 行 862 字——都比 850KB 的 `overview_10y` 强。

所以剔除的理由**不是「体积大」本身**（单次 GET 的大小不进上下文，只影响一次请求的耗时），
而是**形状不对**：这批端点只有「图表区间」层，没有「单点读数」层。模型拿到它只有两条路——
整包被采成首尾 3 天（等于没给），或下钻 `series.*` 拿到几百个点对（2000 字装不下、也没人读）。
顺带纠一个口径：`overview_1m/3m/6m/1y` 其实只有 7–83KB，并不是全都 830KB 起步；
真正到 MB 级的是 `10y` / `30y` / `all` 三个。

### 已知的坑（提示词必须能区分）

1. **`credit/overview` 与 `credit/stress` 是两个不同的 0-100 分，结论会打架。**
   overview 的 `regime.score` = 61.0「中性偏紧」（7 分量等权，定位**信用周期位置**）；
   stress 的 `composite` = 44.7「中性」（5 分量加权，量**当期压力**）。这两个分在同一页上并存
   （AGENTS.md §8 记的「一页两份压力色标」老账，stress 那个已降为图注）。提示词口径：regime
   答「在周期哪一段」，stress 答「现在有多紧」，回答里说明用的是哪个，别把 61/44.7 混成一个数。
2. **`rates/analysis` 是规则引擎研判，不是原始数据。** `overview.sections` 是四段散文，
   `yield_curve` 子树与 `rates/yield-curve` 的 `yield_curve` 同源（同 `as_of`、同 `spreads` 值）。
   问「10Y 多少」钻 `yield-curve`；`analysis` 只用来取「本站怎么解读」。
3. **同一个量两种单位。** `rates/real-rates.spread_2s10` 是 **%**（末条 0.33），
   `rates/yield-curve.yield_curve.spreads.2s10s` 是 **bp**（33）。信用侧一律 bp
   （`hy` 323 = 3.23%）。不带单位回答时，0.33 会被读成 0.33bp。
4. **`as_of` 是生成日，不是读数日。** `rates/real-rates.as_of` 2026-10-19，但序列末点是
   2026-10-15（差 4 天）；`liquidity/fed-balance-sheet` 的 `rows` 末两行 Fed 字段是
   `null`（H.4.1 未发布，只剩 SPX/NDX 快照）。读数日期一律取数据点自带的 `date` / `as_of`。
5. **数组下钻只认非负下标**：`rows.19` 可以，`rows.-1` 返回「JSON 路径不存在」+ 可用键。
   但长数组本来就会被采成 `{first:[3], last:[3]}`，**最新值在 `last` 里**，通常不需要下标。
6. **`rates/auctions.recent` 不能按期限查。** 106 行混 Bill / Note / Bond，`security_term` 是
   「9-Year 10-Month」这类发行名（10Y 的续发就长这样），采样只给最新 3 条（全是 Bill）。
   按期限问需求一律走 `trend.<2Y|5Y|10Y|30Y>`（365 天分桶）、`tail_trend.*`、`indirect_trend.*`。
7. **重复读数**：`liquidity/snapshot.cards` ⊂ `fed-balance-sheet.cards` ∪ `rrp-tga.current`
   （WALCL / RRP / TGA / NET_LIQUIDITY 同一批数）。snapshot 的唯一增量是 3 段 `evaluation`
   文案；问具体某个余额，钻细端点的那一个字段更省字。
8. **规则引擎散文别当数据**：`volatility/dashboard.narrative`（1519 字 / 6 段）、
   `rates/analysis.overview.sections`、`credit/overview.signals` 都是「本站的解读」，
   页面 DOM 摘要里通常已有；当「查到的读数」复述既重复又无法溯源。
9. **`pct_10y` 的窗口可能只有 3 年。** `credit/overview` 的 `ig` / `hy` / `spreads` / `quality`
   都带 `pct_10y` + `obs` + `full_10y`：分位取最近 2500 个观测算，样本不足就按现有样本算并置
   `full_10y: false`（IG / HY 当前 `obs` 842 ≈ 3.3 年）。所以「10 年分位 82.8%」是错的，
   得说「近 842 个交易日的 82.8 分位（10 年窗口未满）」。限定的线索只在这个布尔字段里，
   页面与工具结果都没有替它说这句话。

### `description` 500 字上限：算过了，分工成立

`docs/ai-chat-backend.md` 的工具通道给 `description` 卡了 ≤500 字（两份实现同一条校验）。
拿真实目录量一遍：

| 放哪 | 实测字数 | 说明 |
|---|---|---|
| tool `description`（只写族） | **131** | 「查询本站已导出的数据端点（rates / credit / liquidity / volatility）…结果过大时按提示用 json_path 下钻」 |
| 端点索引（17 条「英文名=中文含义」） | **397**（4 行） | `toolCatalogLegend()` 按 group 分行生成 |
| 上面两项相加 | 528 | **超 28 字**，而且还没算「不确定选哪个就先调最接近的」这类用法说明 |
| 系统提示词整块 `AI_ENDPOINT_INDEX`（表头 + 索引 + 尾注） | **448** | 占 40k 字符预算 1.1% |
| `parameters.properties.endpoint.enum` | **337**（17 个路径） | 在 `parameters` 里，服务端**不校验**其长度 |

**结论：分工成立，而且是被上限逼出来的。** 131 + 397 = 528 > 500，索引硬塞
`description` 当场 422；而索引本来也不该吃那个额度——`enum` 已经把 17 个路径下发了
（337 字走 `parameters`，不占 description），description 缺的只是「中文含义 + 哪族查什么」，
这部分放系统提示词一次讲清，还能顺带放调用纪律（「不确定就先调最接近的」）。两边都是
每轮随请求下发，所以不是省不省 token 的问题，是**硬上限放不下**。

按 23 字/端点算，再加 10 个端点索引会涨到 ~630 字（仍在 system prompt 里，description 早就爆了）：
**以后扩端点只扩索引，不扩 description。** 这也是 `TOOL_CATALOG` 单源的意义——
索引由目录生成，加端点不会有人忘记同步提示词。

### 遗留：端点数据的可读性（不在目录层，留给 T3 / 数据层）

审计 17 个端点时发现、但**改工具目录解决不了**的 8 条。都记在这里，别让它们随着审计报告蒸发：

1. `liquidity/subsurface.percentiles.values` 的键是中文（`1分位` / `中位数`），`json_path` 也得写中文，
   模型容易转义错或拼错。数据层应加英文键或别名。
2. **同一个量两种类型**：`transmission-chain.evidence.balancesheet.reserves_b` 是字符串 `"3,022.1"`，
   而 `liquidity/reserves.cards.reserves.value_b` 是数字 `3022.14`。模型会拿字符串做算术。
3. UI 字段混进数据端点：`transmission-chain.band` 是 `["中性","blue"]`、`volatility/dashboard.zones`
   是纯图例 `{label,color}`——颜色名进模型上下文只是噪声。
4. `volatility/dashboard` 有两个日期（`as_of` 与 `as_of_snapshot`），哪个是读数日、哪个是快照日，
   页面和文档都没写明。
5. **执行器没有 per-turn 缓存**：同一端点查两次算两次调用（每轮只有 3 次）。属 T3。
6. `credit/overview.sloos` 数组里有 `null` 元素（如 `CRE 贷款标准: null`），模型容易读成 0。
7. `rates/auctions.avg_cover_10_coupon` 的名字读起来像「10Y 的平均认购倍数」，实际是
   「最近 10 场名义付息券」——提示词若要引用必须复述口径。
8. `rates/auctions.recent` 是「近 90 天」而不是「最近 106 场」，两者不等价。

前四条是**后端数据形状**问题（`src/server.py` 与各 `*_analysis.py` 的产出口径），要修得单独开票；
第 5 条归 T3；第 6–8 条改提示词或字段名即可，成本很低。

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
| T1 | `query_site_data` 单工具 + 浏览器循环 + 契约放行 | T0（**已完成 2026-10-22**） |
| T2 | 工具按族扩到 rates / credit / liquidity（17 个端点，见「工具目录 v2」） | T1（**已完成 2026-10-22**） |
| T3 | 失败与降级（404 / 超时 / 截断 / 轮数上限） | T1（**已完成 2026-10-22**） |
| T4 | 跨端点工具（相关性矩阵、期权墙） | T2 |
| T5 | 提示词口径改写 + 文档 + 测试收尾 | T1–T4 |

T1 是 tracer bullet：它证明了「模型发 `tool_calls` → 页面取数 → 回填 → 续流」这条链能通，
T2 之后全是往枚举里加值。**T1 通了但 T2 没做完，功能仍然可用**（一个端点也能答收益率问题）。
