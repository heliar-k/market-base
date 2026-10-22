"""站内数据工具（frontend/public/js/ai-tools.js）的纯逻辑测试 —— node 直接跑。

覆盖 T1 前端最容易回归的八处：
- 工具目录 schema 与端点白名单（模型只能选 enum 里的端点）；
- json_path 下钻 / 大结果摘要 / 长数组首尾采样（不硬截半截 JSON）；
- 对象数组摘要带首元素键（避免模型为猜子键多花一轮预算）；
- 404 / 网络错误 / 超时归形成 {error, endpoint, status}，绝不抛；
- 网络层失败只重试一次，且剩余预算 <2s / 用户 abort 时不重试；
- SSE tool_calls 分片按 index 拼回完整 arguments，并记录 finish_reason；
- 每轮 ≤3 次、≤2 轮往返的限额（planToolRound）；
- 预算用尽仍要工具时 nextLoopAction 强制下一轮 tool_choice:'none'。
顺带锁住提示词三层优先级与 chatPayload 的 tools 单源接线。

T2 扩到 17 端点后补四条守卫：
- 目录路径唯一、格式合法、四族齐全、17 条，且每条都能在 src/export_pages.py 找到导出；
- toolCatalogLegend() 覆盖每个 group / label，且提示词里的索引确实由它生成
  （ai-prompt.js 源码不许出现任何端点路径字面量）；
- tool description 仍在服务端 500 字上限内（索引只能放提示词）；
- 大端点（80KB 级）仍返回结构摘要而不是前 2000 字半截 JSON。

T3「失败与降级」再补五处（单次超时 / 同轮缓存 / 失败可观测）：
- 单次尝试 3.5s 上限独立于 8s 批次预算，卡死不再吃光整轮；超时后仍能重试一次；
- 永不 resolve 的 fetchImpl 也不会把 runToolCall 挂死（执行器自己判 abort）；
- 同轮 endpoint+json_path 缓存命中不发请求、callId 仍是本次调用的；换路径/换 Map 不命中；
- 失败结果同样缓存（同一轮里同端点结果不会变）；
- ok 标志与 summarizeToolRuns() 把成功/失败端点分开（msg.tools / msg.toolsFailed）。

T4「两个跨端点工具」再补十处（参数解析 / 客户端连接 / 压缩 / 排序 / 缓存隔离）：
- TOOLS 从 1 个变 3 个，两个新工具的名字、参数与 description 一字不改；
- 相关性矩阵做「下标→名字」连接，pairs 里的名字与系数对得上（不是下标）；
- pairs 按 |corr| 降序、最多 8 条、排除自身，null/NaN 的格子跳过；
- 不给 symbol 时返回 4 条 alerts（不带 series 序列）+ 最多 5 条 strongest（不含对角线）；
- 未知标的 → 结构化错误 + available 全部名字（相关性 22 个 / 期权 22 个），ok === false；
- 期权摘要只留 21 个结构字段 + oi_top 前 5 + iv_curve 前 3，丢掉 wall / gex_dist /
  narrative，序列化后 ≤2000 字（不再触发结构摘要）；
- oi_top 端点给的不是降序：按 total 降序取前 5，缺 total / 非数字不崩且排后面；
  iv_curve 按 dte 升序取前 3；
- 新工具不另写执行器：单次 3.5s 超时与 404 归形都走同一套（T3 行为不退）；
- 缓存键带工具名：同轮 query_correlation 两次只 1 次网络，两个新工具各自独立键；
- 提示词加一句三者分工（工具名可硬编码，端点名仍只能由 legend 生成）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "frontend" / "public" / "js"
TOOLS = JS / "ai-tools.js"
PROMPT = JS / "ai-prompt.js"
CONFIG = JS / "ai-config.js"
EXPORT_PAGES = ROOT / "src" / "export_pages.py"

# 与 frontend/public/js/ai-tools.js 的 TOOL_CATALOG 逐条对应（顺序也计入：
# 提示词索引按目录顺序分行，模型看到的族顺序应当稳定）。
EXPECTED_PATHS = [
    "rates/yield-curve",
    "rates/real-rates",
    "rates/fed-funds",
    "rates/auctions",
    "rates/analysis",
    "credit/overview",
    "credit/stress",
    "credit/cds",
    "liquidity/snapshot",
    "liquidity/reserves",
    "liquidity/rrp-tga",
    "liquidity/fed-balance-sheet",
    "liquidity/operations",
    "liquidity/subsurface",
    "liquidity/transmission-chain",
    "liquidity/global-dollar",
    "volatility/dashboard",
]
PATH_RE = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")

BODY = """
const tools = await import('__TOOLS__');
const prompt = await import('__PROMPT__');
const config = await import('__CONFIG__');

// ── 1. 工具目录 schema ───────────────────────────────────────────────────
const fn = tools.TOOLS[0].function;
const catalog = {
  count: tools.TOOLS.length,
  type: tools.TOOLS[0].type,
  name: fn.name,
  names: tools.TOOLS.map((t) => t.function.name),
  types: tools.TOOLS.map((t) => t.type),
  requiredByTool: tools.TOOLS.map((t) => t.function.parameters.required),
  propsByTool: tools.TOOLS.map((t) => Object.keys(t.function.parameters.properties)),
  descLens: tools.TOOLS.map((t) => t.function.description.length),
  enum: fn.parameters.properties.endpoint.enum,
  required: fn.parameters.required,
  jsonPathType: fn.parameters.properties.json_path.type,
  jsonPathRequired: (fn.parameters.required || []).includes('json_path'),
  descHasPath: fn.description.includes('JSON'),
  descLen: fn.description.length,
  maxChars: tools.TOOL_MAX_CHARS,
  maxCalls: tools.MAX_CALLS_PER_ROUND,
  maxRounds: tools.MAX_TOOL_ROUNDS,
  timeout: tools.TOOL_TIMEOUT_MS,
  byName: !!tools.toolByName('query_site_data'),
  byNameAll: tools.TOOLS.every((t) => !!tools.toolByName(t.function.name)),
  byNameMissing: tools.toolByName('nope') === null,
};

// ── 1b. 目录元数据与提示词索引（T2：单源，17 端点） ──────────────────────
const catalogMeta = {
  entries: tools.TOOL_CATALOG.map((t) => ({ path: t.path, group: t.group, label: t.label })),
  count: tools.TOOL_CATALOG.length,
  endpointsMatch:
    JSON.stringify(tools.TOOL_ENDPOINTS) ===
    JSON.stringify(tools.TOOL_CATALOG.map((t) => t.path)),
  enumMatch:
    JSON.stringify(fn.parameters.properties.endpoint.enum) ===
    JSON.stringify(tools.TOOL_ENDPOINTS),
  legend: tools.toolCatalogLegend(),
};

// ── 2. json_path 下钻 ────────────────────────────────────────────────────
const sample = {
  data: {
    current: { value: 4.812 },
    series: [{ date: '2026-10-20', value: 1 }, { date: '2026-10-21', value: 2 }],
  },
  nil: null,
  as_of: '2026-10-21',
};
const drill = {
  deep: tools.jsonPath(sample, 'data.current.value'),
  index: tools.jsonPath(sample, 'data.series.1.value'),
  empty: tools.jsonPath(sample, ''),
  badIndex: tools.jsonPath(sample, 'data.series.9'),
  badKey: tools.jsonPath(sample, 'data.missing'),
  throughNull: tools.jsonPath(sample, 'nil.x'),
};

// ── 3. 截断与体积策略 ────────────────────────────────────────────────────
const small = { a: 1, b: [1, 2] };
const longArray = [];
for (let i = 0; i < 365; i += 1) {
  longArray.push({ date: '2026-01-01', value: i });
}
const big = {
  as_of: '2026-10-21',
  nominal_10y: longArray,
  nested: { a: 1, b: [1, 2, 3] },
  note: 'x'.repeat(300),
};
const bigRaw = JSON.stringify(big);
const summaryText = tools.renderToolResult(big);
const summary = JSON.parse(summaryText);
const arrText = tools.renderToolResult(longArray);
const arr = JSON.parse(arrText);
const hugeString = tools.renderToolResult('y'.repeat(5000));
const trunc = tools.truncateText('x'.repeat(5000));
const volume = {
  smallUntouched: tools.renderToolResult(small) === JSON.stringify(small),
  summaryLen: summaryText.length,
  summaryTotal: summary.total_chars,
  summaryRawLen: bigRaw.length,
  summaryNote: summary._note,
  summaryAsOf: summary.summary.as_of,
  summaryArrayType: summary.summary.nominal_10y,
  summaryObjectType: summary.summary.nested,
  summaryNoteTruncated: summary.summary.note.endsWith('…'),
  arrayLen: arrText.length,
  arrayTotal: arr.total,
  arrayFirst: arr.first.length,
  arrayLast: arr.last.length,
  arrayFirstValue: arr.first[0].value,
  arrayLastValue: arr.last[arr.last.length - 1].value,
  arrayNote: arr._note,
  hugeLen: hugeString.length,
  hugeParses: typeof JSON.parse(hugeString) === 'string',
  hugeHasMarker: JSON.parse(hugeString).includes('<truncated'),
  truncLen: trunc.length,
  truncMarker: trunc.includes('<truncated'),
  exactUntouched: tools.truncateText('z'.repeat(2000)).length === 2000,
};

// ── 4. 执行器：成功 / 404 / 断网 / 超时 / 非法端点 / 路径不存在 ──────────
const call = (args) => ({
  id: 'call_test',
  type: 'function',
  function: { name: 'query_site_data', arguments: JSON.stringify(args) },
});
let seenUrl = '';
const okRun = await tools.runToolCall(call({ endpoint: 'rates/real-rates', json_path: 'as_of' }), {
  fetchImpl: async (url) => {
    seenUrl = url;
    return { ok: true, status: 200, json: async () => ({ as_of: '2026-10-21' }) };
  },
});
const notFound = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }),
});
const network = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  fetchImpl: async () => {
    throw new TypeError('Failed to fetch');
  },
});
const timeout = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  fetchImpl: async () => {
    const e = new Error('aborted');
    e.name = 'AbortError';
    throw e;
  },
});
let evilCalled = false;
const evil = await tools.runToolCall(call({ endpoint: 'https://evil.example/x' }), {
  fetchImpl: async () => {
    evilCalled = true;
    return { ok: true, status: 200, json: async () => ({}) };
  },
});
// 目录外但「长得像真端点」的路径同样拒绝（liquidity/overview_* 是 830KB–2MB 图表数据）
let overviewCalled = false;
const overview = await tools.runToolCall(call({ endpoint: 'liquidity/overview_all' }), {
  fetchImpl: async () => {
    overviewCalled = true;
    return { ok: true, status: 200, json: async () => ({}) };
  },
});
const missing = await tools.runToolCall(
  call({ endpoint: 'rates/real-rates', json_path: 'data.nope' }),
  {
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ data: { a: 1, b: 2 } }),
    }),
  }
);
const badJson = await tools.runToolCall(
  { id: 'call_bad', function: { name: 'query_site_data', arguments: '{not json' } },
  { fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }) }
);
const unknown = await tools.runToolCall(
  { id: 'call_u', function: { name: 'fetch_any_url', arguments: '{}' } },
  { fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }) }
);
const execute = {
  okContent: JSON.parse(okRun.content),
  okEndpoint: okRun.endpoint,
  okCallId: okRun.callId,
  seenUrl,
  nf: JSON.parse(notFound.content),
  networkError: JSON.parse(network.content),
  timeoutError: JSON.parse(timeout.content),
  evilCalled,
  evil: JSON.parse(evil.content),
  overviewCalled,
  overview: JSON.parse(overview.content),
  missing: JSON.parse(missing.content),
  badJson: JSON.parse(badJson.content),
  unknown: JSON.parse(unknown.content),
};

// ── 4b. 网络层有界重试（一次，且剩余预算 ≥2s） ───────────────────────────
let retryOkCount = 0;
const retryOk = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 5000,
  fetchImpl: async () => {
    retryOkCount += 1;
    if (retryOkCount === 1) {
      const e = new Error('stalled before request');
      e.name = 'AbortError';
      throw e;
    }
    return { ok: true, status: 200, json: async () => ({ value: 5.43 }) };
  },
});
let retryShortCount = 0;
const retryShort = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 500,
  fetchImpl: async () => {
    retryShortCount += 1;
    throw new TypeError('Failed to fetch');
  },
});
const aborter = new AbortController();
aborter.abort();
let retryAbortCount = 0;
const retryAbort = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 5000,
  outerSignal: aborter.signal,
  fetchImpl: async () => {
    retryAbortCount += 1;
    const e = new Error('aborted');
    e.name = 'AbortError';
    throw e;
  },
});
let retryFailCount = 0;
const retryFail = await tools.runToolCall(call({ endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 5000,
  fetchImpl: async () => {
    retryFailCount += 1;
    throw new TypeError('Failed to fetch');
  },
});
const retry = {
  okContent: JSON.parse(retryOk.content),
  okCount: retryOkCount,
  shortCount: retryShortCount,
  shortError: JSON.parse(retryShort.content),
  abortCount: retryAbortCount,
  abortError: JSON.parse(retryAbort.content),
  failCount: retryFailCount,
  failError: JSON.parse(retryFail.content),
  can: {
    first: tools.canRetryTool({
      attempt: 1,
      status: 0,
      userAborted: false,
      signalAborted: false,
      remainingMs: 2500,
    }),
    second: tools.canRetryTool({
      attempt: 2,
      status: 0,
      userAborted: false,
      signalAborted: false,
      remainingMs: 9000,
    }),
    userAbort: tools.canRetryTool({
      attempt: 1,
      status: 0,
      userAborted: true,
      signalAborted: false,
      remainingMs: 9000,
    }),
    signalAbort: tools.canRetryTool({
      attempt: 1,
      status: 0,
      userAborted: false,
      signalAborted: true,
      remainingMs: 9000,
    }),
    short: tools.canRetryTool({
      attempt: 1,
      status: 0,
      userAborted: false,
      signalAborted: false,
      remainingMs: 1999,
    }),
    notNetwork: tools.canRetryTool({
      attempt: 1,
      status: 404,
      userAborted: false,
      signalAborted: false,
      remainingMs: 9000,
    }),
  },
  budgetDeadline: tools.retryBudgetLeft({ deadline: Date.now() + 5000 }) > 4000,
  budgetMs: tools.retryBudgetLeft({ budgetMs: 3000 }) === 3000,
  budgetNone: tools.retryBudgetLeft({}),
};

// ── 5. SSE tool_calls 分片累积 ───────────────────────────────────────────
const line = (obj) => 'data: ' + JSON.stringify(obj);
const frags = [
  line({ choices: [{ delta: { tool_calls: [
    {
      index: 0,
      id: 'call_a',
      type: 'function',
      function: { name: 'query_site_data', arguments: '{"endpoint":"rates/yi' },
    },
  ] } }] }),
  line({ choices: [{ delta: { tool_calls: [
    { index: 0, function: { arguments: 'eld-curve","json_path":"yield_curve.spreads"}' } },
  ] } }] }),
  line({ choices: [{ delta: {}, finish_reason: 'tool_calls' }] }),
  'data: [DONE]',
  'not-a-data-line',
];
let acc = [];
let finish = '';
for (const l of frags) {
  const d = tools.parseSseLine(l);
  if (d && d !== '[DONE]') {
    acc = tools.accumulateToolCalls(acc, d.toolCalls);
    if (d.finishReason) finish = d.finishReason;
  }
}
const finalized = tools.finalizeToolCalls(acc);
const interleaved = tools.finalizeToolCalls(
  tools.accumulateToolCalls(
    tools.accumulateToolCalls([], [
      {
        index: 1,
        id: 'call_b',
        type: 'function',
        function: {
          name: 'query_site_data',
          arguments: '{"endpoint":"volatility/dashboard"}',
        },
      },
    ]),
    [
      {
        index: 0,
        id: 'call_a',
        type: 'function',
        function: { name: 'query_site_data', arguments: '{"endpoint":"rates/yield-curve"}' },
      },
    ]
  )
);
const missingArgs = tools.normalizeDelta(
  {
    tool_calls: [
      { index: 0, id: 'call_x', function: { name: 'query_site_data' } },
    ],
  },
  ''
);
const sse = {
  done: tools.parseSseLine('data: [DONE]'),
  nonData: tools.parseSseLine('event: ping'),
  broken: tools.parseSseLine('data: {oops'),
  finalCount: finalized.length,
  args: JSON.parse(finalized[0].function.arguments),
  id: finalized[0].id,
  name: finalized[0].function.name,
  finish,
  missingArgs: missingArgs.toolCalls[0].function.arguments,
  order: interleaved.map((c) => c.id),
  orderArgs: interleaved.map((c) => JSON.parse(c.function.arguments).endpoint),
};

// ── 6. 限额 ──────────────────────────────────────────────────────────────
const four = [1, 2, 3, 4].map((i) => ({ id: 'c' + i }));
const limits = {
  first: tools.planToolRound(four, 0),
  second: tools.planToolRound([{ id: 'c9' }], 1),
  exhausted: tools.planToolRound([{ id: 'c9' }], 2),
  empty: tools.planToolRound([], 0),
  custom: tools.planToolRound(four, 0, { maxCalls: 1, maxRounds: 1 }),
  choices: {
    start: tools.nextToolChoice(0, true),
    mid: tools.nextToolChoice(1, true),
    exhausted: tools.nextToolChoice(2, true),
    noCalls: tools.nextToolChoice(2, false),
    over: tools.nextToolChoice(5, true),
    custom: tools.nextToolChoice(1, true, 1),
  },
  loop: {
    tools: tools.nextLoopAction([{ id: 'c1' }], 0, false),
    mid: tools.nextLoopAction([{ id: 'c1' }], 1, false),
    force: tools.nextLoopAction([{ id: 'c1' }], 2, false),
    stop: tools.nextLoopAction([], 2, false),
    afterForce: tools.nextLoopAction([{ id: 'c1' }], 2, true),
  },
};
const describe = {
  objectArray: tools.describeValue([
    { tenor: '30Y', yield: 5.48, change: 0.01 },
    { tenor: '10Y', yield: 4.2, change: -0.01 },
  ]),
  primitiveArray: tools.describeValue([1, 2, 3]),
  emptyArray: tools.describeValue([]),
};

// ── 7. 上游 400 兜底（spec 第 6 条残余风险） ────────────────────────────
const minimal = tools.minimalMessages([
  { role: 'system', content: 'sys' },
  { role: 'user', content: 'u1' },
  { role: 'assistant', content: 'a1' },
  { role: 'tool', content: 't1', tool_call_id: 'call_1' },
  { role: 'user', content: 'u2' },
  { role: 'assistant', content: 'a2', tool_calls: [] },
]);
const degrade = {
  hit: tools.isReasoningReject(400, '{"error":"reasoning_content is required"}'),
  spaced: tools.isReasoningReject(400, 'reasoning content required'),
  other400: tools.isReasoningReject(400, 'invalid tools schema'),
  wrongStatus: tools.isReasoningReject(401, 'reasoning_content'),
  minimalRoles: minimal.map((m) => m.role),
  minimalLast: minimal[1].content,
  minimalEmpty: tools.minimalMessages([]),
};

// ── 8. 提示词与请求体接线 ────────────────────────────────────────────────
const payloadProxy = config.chatPayload(
  { model: 'm1', effort: 'high' },
  [{ role: 'user', content: 'hi' }],
  [],
  true
);
const payloadDirect = config.chatPayload(
  { model: 'm1', effort: 'high' },
  [{ role: 'user', content: 'hi' }],
  [{ id: 'm1', reasoning_levels: ['high'] }],
  false
);
const wiring = {
  version: prompt.AI_PROMPT_VERSION,
  system: prompt.AI_SYSTEM_PROMPT,
  factsHint: prompt.AI_FACTS_HINT,
  hasBudget: prompt.AI_SYSTEM_PROMPT.includes('最多查两次'),
  proxyTool: payloadProxy.tools[0].function.name,
  proxyChoice: payloadProxy.tool_choice,
  proxyEffort: Object.prototype.hasOwnProperty.call(payloadProxy, 'reasoning_effort'),
  directTool: payloadDirect.tools[0].function.name,
  directChoice: payloadDirect.tool_choice,
  directEffort: payloadDirect.reasoning_effort,
  forcedChoice: config.chatPayload(
    { model: 'm1' },
    [{ role: 'user', content: 'hi' }],
    [],
    true,
    undefined,
    'none'
  ).tool_choice,
};

// ── 8b. 端点索引进提示词（由 TOOL_CATALOG 生成，不手抄） ────────────────
const promptIndex = {
  text: prompt.AI_ENDPOINT_INDEX,
  inSystem: prompt.AI_SYSTEM_PROMPT.includes(prompt.AI_ENDPOINT_INDEX),
  hasLegend: prompt.AI_SYSTEM_PROMPT.includes(tools.toolCatalogLegend()),
  fallbackHint: prompt.AI_SYSTEM_PROMPT.includes('不确定选哪个就先调最接近的'),
  lines: prompt.AI_ENDPOINT_INDEX.split('\\n').length,
};

// ── 9. 大端点仍走结构摘要（80KB 级真实体积） ─────────────────────────────
const bigPayload = {
  as_of: '2026-10-21',
  narrative: 'x'.repeat(4000),
  series: Array.from({ length: 1800 }, (_, i) => ({
    date: '2026-01-01',
    value: i,
    label: 'row',
  })),
};
const bigRun = await tools.runToolCall(call({ endpoint: 'liquidity/transmission-chain' }), {
  fetchImpl: async () => ({ ok: true, status: 200, json: async () => bigPayload }),
});
const bigSummary = JSON.parse(bigRun.content);
const bigEndpoint = {
  rawLen: JSON.stringify(bigPayload).length,
  len: bigRun.content.length,
  endpoint: bigRun.endpoint,
  note: bigSummary._note,
  total: bigSummary.total_chars,
  asOf: bigSummary.summary.as_of,
  seriesType: bigSummary.summary.series,
};

console.log(
  JSON.stringify({
    catalog,
    catalogMeta,
    drill,
    volume,
    execute,
    retry,
    sse,
    limits,
    degrade,
    describe,
    wiring,
    promptIndex,
    bigEndpoint,
  })
);
"""


def _run() -> dict:
    script = (
        BODY.replace("__TOOLS__", TOOLS.as_uri())
        .replace("__PROMPT__", PROMPT.as_uri())
        .replace("__CONFIG__", CONFIG.as_uri())
    )
    return run_node(script, timeout=30)


# T3 单独一段脚本：这里有真实计时（单次超时 60ms 的测试覆写），不必让上面每个测试
# 都重跑一遍；lru_cache 让它只跑一次（纯逻辑、无随机，重复跑没有价值）。
T3_BODY = """
const tools = await import('__TOOLS__');

const call = (id, args) => ({
  id,
  type: 'function',
  function: { name: 'query_site_data', arguments: JSON.stringify(args) },
});

// ── 1. 限额常量：单次 3.5s 新增，其余三个值不动 ─────────────────────────
const constants = {
  callTimeout: tools.TOOL_CALL_TIMEOUT_MS,
  roundBudget: tools.TOOL_TIMEOUT_MS,
  maxChars: tools.TOOL_MAX_CHARS,
  maxCalls: tools.MAX_CALLS_PER_ROUND,
  maxRounds: tools.MAX_TOOL_ROUNDS,
};

// ── 2. 永不 resolve 的 fetch：单次超时掐断，且只烧掉这一次尝试 ─────────
// callTimeoutMs 是测试覆写（生产走 TOOL_CALL_TIMEOUT_MS=3500），否则这条要真等几秒。
// 剩余预算 500ms < 2s 时重试规则不触发：正好量「一次卡死 ≈ 单次上限」而不是 8s。
let timeoutAttempts = 0;
const timeoutStarted = Date.now();
const timed = await tools.runToolCall(call('call_timeout', { endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 500,
  callTimeoutMs: 60,
  fetchImpl: () => {
    timeoutAttempts += 1;
    return new Promise(() => {});
  },
});
const timeout = {
  ok: timed.ok,
  error: JSON.parse(timed.content),
  attempts: timeoutAttempts,
  elapsedMs: Date.now() - timeoutStarted,
};

// ── 2b. 剩余预算充足：两次都超时才落成错误，且只重试一次 ────────────────
let twiceAttempts = 0;
const twiceStarted = Date.now();
const twice = await tools.runToolCall(
  call('call_timeout_twice', { endpoint: 'rates/yield-curve' }),
  {
    deadline: Date.now() + 8000,
    callTimeoutMs: 60,
    fetchImpl: () => {
      twiceAttempts += 1;
      return new Promise(() => {});
    },
  }
);
const timeoutTwice = {
  ok: twice.ok,
  error: JSON.parse(twice.content),
  attempts: twiceAttempts,
  elapsedMs: Date.now() - twiceStarted,
};

// ── 3. 第一次挂住、第二次成功：单次超时后仍能走重试规则救回来 ──────────
let retryAttempts = 0;
const retryStarted = Date.now();
const rescued = await tools.runToolCall(call('call_rescue', { endpoint: 'rates/yield-curve' }), {
  deadline: Date.now() + 8000,
  callTimeoutMs: 60,
  fetchImpl: () => {
    retryAttempts += 1;
    if (retryAttempts === 1) return new Promise(() => {});
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ value: 5.43 }),
    });
  },
});
const rescue = {
  ok: rescued.ok,
  content: JSON.parse(rescued.content),
  attempts: retryAttempts,
  elapsedMs: Date.now() - retryStarted,
};

// ── 4. 同轮缓存：同 endpoint+json_path 第二次不发请求 ────────────────────
const cache = new Map();
let cacheFetches = 0;
const cacheFetch = async () => {
  cacheFetches += 1;
  return { ok: true, status: 200, json: async () => ({ as_of: '2026-10-21' }) };
};
const cachedFirst = await tools.runToolCall(
  call('call_cache_1', { endpoint: 'rates/real-rates', json_path: 'as_of' }),
  { fetchImpl: cacheFetch, cache }
);
const cachedSecond = await tools.runToolCall(
  call('call_cache_2', { endpoint: 'rates/real-rates', json_path: 'as_of' }),
  { fetchImpl: cacheFetch, cache }
);
// 换 json_path 是另一个键
const cachedOtherPath = await tools.runToolCall(
  call('call_cache_3', { endpoint: 'rates/real-rates', json_path: 'data' }),
  { fetchImpl: cacheFetch, cache }
);
// 换新 Map（= 新的一轮）不命中
const cachedNewTurn = await tools.runToolCall(
  call('call_cache_4', { endpoint: 'rates/real-rates', json_path: 'as_of' }),
  { fetchImpl: cacheFetch, cache: new Map() }
);
const cacheOut = {
  fetches: cacheFetches,
  first: { ok: cachedFirst.ok, content: cachedFirst.content, callId: cachedFirst.callId },
  second: {
    ok: cachedSecond.ok,
    content: cachedSecond.content,
    callId: cachedSecond.callId,
    cached: cachedSecond.cached,
  },
  otherPath: {
    ok: cachedOtherPath.ok,
    content: JSON.parse(cachedOtherPath.content),
    cached: cachedOtherPath.cached,
  },
  newTurn: {
    ok: cachedNewTurn.ok,
    content: cachedNewTurn.content,
    cached: cachedNewTurn.cached,
  },
};

// ── 4b. 同一批并发两个相同调用：只发一次 GET，callId 各自对上 ──────────
// 模型同轮完全可能吐两条参数一样的 tool_calls；缓存必须「先占位再 await」才能去重。
const concurrentCache = new Map();
let concurrentFetches = 0;
const concurrentFetch = async () => {
  concurrentFetches += 1;
  return { ok: true, status: 200, json: async () => ({ value: 42 }) };
};
const [dupA, dupB] = await Promise.all([
  tools.runToolCall(call('call_dup_a', { endpoint: 'credit/stress' }), {
    fetchImpl: concurrentFetch,
    cache: concurrentCache,
  }),
  tools.runToolCall(call('call_dup_b', { endpoint: 'credit/stress' }), {
    fetchImpl: concurrentFetch,
    cache: concurrentCache,
  }),
]);
const concurrent = {
  fetches: concurrentFetches,
  a: { callId: dupA.callId, content: dupA.content, cached: dupA.cached },
  b: { callId: dupB.callId, content: dupB.content, cached: dupB.cached },
};

// ── 5. 失败也缓存（404 两次只请求一次） ─────────────────────────────────
const failCache = new Map();
let failFetches = 0;
const failFetch = async () => {
  failFetches += 1;
  return { ok: false, status: 404, json: async () => ({}) };
};
const failFirst = await tools.runToolCall(
  call('call_fail_1', { endpoint: 'rates/auctions' }),
  { fetchImpl: failFetch, cache: failCache }
);
const failSecond = await tools.runToolCall(
  call('call_fail_2', { endpoint: 'rates/auctions' }),
  { fetchImpl: failFetch, cache: failCache }
);
const failCacheOut = {
  fetches: failFetches,
  first: { ok: failFirst.ok, content: failFirst.content, cached: failFirst.cached },
  second: { ok: failSecond.ok, content: failSecond.content, cached: failSecond.cached },
};

// ── 6. ok 标志：200 → true，404 → false ──────────────────────────────────
const okFlag = {
  success: (
    await tools.runToolCall(call('call_ok', { endpoint: 'liquidity/snapshot' }), {
      fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ cards: [] }) }),
    })
  ).ok,
  notFound: (
    await tools.runToolCall(call('call_nf', { endpoint: 'liquidity/snapshot' }), {
      fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }),
    })
  ).ok,
};

// ── 7. 成功/失败端点分组（runTurn 的 msg.tools / msg.toolsFailed 单源） ──
const summarize = {
  mixed: tools.summarizeToolRuns([
    { endpoint: 'rates/yield-curve', ok: true },
    { endpoint: 'credit/stress', ok: false },
    { endpoint: 'rates/yield-curve', ok: true },
    { endpoint: '', ok: false },
    { endpoint: 'credit/cds', content: '{"error":"x","endpoint":"credit/cds","status":0}' },
    { endpoint: 'liquidity/snapshot', content: '{"cards":[]}' },
  ]),
  both: tools.summarizeToolRuns([
    { endpoint: 'rates/auctions', ok: false },
    { endpoint: 'rates/auctions', ok: true },
  ]),
  empty: tools.summarizeToolRuns(null),
};

console.log(
  JSON.stringify({
    constants,
    timeout,
    timeoutTwice,
    rescue,
    cache: cacheOut,
    concurrent,
    failCache: failCacheOut,
    okFlag,
    summarize,
  })
);
"""


@lru_cache(maxsize=1)
def _run_t3() -> dict:
    return run_node(T3_BODY.replace("__TOOLS__", TOOLS.as_uri()), timeout=30)


# T4 单独一段脚本：两个跨端点工具的参数解析 / 连接 / 压缩 / 缓存隔离都在这里。
# 假 payload 按真实形状裁（3×3 矩阵、2 个标的的期权快照），不依赖 public/api 里
# 的当日产物（干净 clone 没有 frontend/public/api）。
T4_BODY = """
const tools = await import('__TOOLS__');
const prompt = await import('__PROMPT__');

const named = (name, id, args) => ({
  id,
  type: 'function',
  function: { name, arguments: JSON.stringify(args) },
});
const fetchConst = (payload) => async () => ({
  ok: true,
  status: 200,
  json: async () => payload,
});

// ── 1. 3×3 假矩阵：连接正确性（名字与系数，不是下标） ────────────────────
const baseAssets = [
  { name: 'SPX', group: 'equity', label: '标普500' },
  { name: 'NDX', group: 'equity', label: '纳指100' },
  { name: 'TLT', group: 'bond', label: '长期国债' },
];
const baseMatrix = [
  [1.0, 0.92, 0.21],
  [0.92, 1.0, 0.05],
  [0.21, 0.05, 1.0],
];
const baseAlerts = [
  {
    key: 'SPX_TLT_30d',
    label: '股债相关性',
    latest: 0.2151,
    prev: 0.201,
    series: [{ date: '2026-01-01', value: 0.1 }],
  },
  { key: 'WTI_SPX_30d', label: '油股相关性', latest: -0.4488, prev: -0.4455, series: [] },
  { key: 'DXY_HYG_30d', label: '美元-信用', latest: -0.5577, prev: -0.5459, series: [] },
  { key: 'MOVE_SPX_30d', label: '债市波动-股票', latest: -0.2761, prev: -0.2629, series: [] },
];
const baseCross = {
  as_of: '2026-10-18',
  window: 30,
  assets: baseAssets,
  groups: { equity: '美股', bond: '国债' },
  matrix: baseMatrix,
  alerts: baseAlerts,
};

const bySymbol = await tools.runToolCall(
  named('query_correlation', 'call_corr_1', { symbol: 'ndx' }),
  { fetchImpl: fetchConst(baseCross) }
);
const corr = JSON.parse(bySymbol.content);
const connect = {
  ok: bySymbol.ok,
  endpoint: bySymbol.endpoint,
  symbol: corr.symbol,
  label: corr.label,
  group: corr.group,
  asOf: corr.as_of,
  window: corr.window,
  pairNames: corr.pairs.map((p) => p.name),
  pairLabels: corr.pairs.map((p) => p.label),
  pairCorrs: corr.pairs.map((p) => p.corr),
  // 没做连接的话这里会是 0/1 这类下标，pairs 里的名字必须都是标的代码
  allNamesAreTickers: corr.pairs.every((p) => typeof p.name === 'string' && p.name.length >= 2),
  selfExcluded: corr.pairs.every((p) => p.name !== 'NDX'),
  noRawMatrix: bySymbol.content.indexOf('[[1') < 0,
};

// ── 2. 排序 / 截断：|corr| 降序、最多 8 条、排除自身 ──────────────────────
const many = Array.from({ length: 10 }, (_, i) => ({
  name: 'A' + i,
  group: 'equity',
  label: '标的' + i,
}));
const row = [1.0, -0.95, 0.91, -0.87, 0.83, -0.79, 0.75, -0.71, 0.67, 0.63];
const manyMatrix = many.map((_, i) =>
  many.map((_, j) => {
    if (i === j) return 1.0;
    if (i === 0) return row[j];
    if (j === 0) return row[i];
    return 0.5;
  })
);
const truncated = JSON.parse(
  (
    await tools.runToolCall(named('query_correlation', 'call_corr_2', { symbol: 'A0' }), {
      fetchImpl: fetchConst({
        as_of: '2026-10-18',
        window: 30,
        assets: many,
        groups: {},
        matrix: manyMatrix,
        alerts: [],
      }),
    })
  ).content
);
const truncation = {
  count: truncated.pairs.length,
  names: truncated.pairs.map((p) => p.name),
  corrs: truncated.pairs.map((p) => p.corr),
  selfExcluded: truncated.pairs.every((p) => p.name !== 'A0'),
  droppedWeakest: truncated.pairs.every((p) => p.name !== 'A9'),
  sortedDesc: truncated.pairs.every(
    (p, i, arr) => i === 0 || Math.abs(arr[i - 1].corr) >= Math.abs(p.corr)
  ),
};

// null / NaN 的格子跳过，不编一个 0 出来
const gapPayload = {
  as_of: '2026-10-18',
  window: 30,
  assets: baseAssets,
  matrix: [
    [1.0, null, Number.NaN],
    [null, 1.0, 0.5],
    [Number.NaN, 0.5, 1.0],
  ],
  alerts: [],
};
const gapPairs = JSON.parse(
  (
    await tools.runToolCall(named('query_correlation', 'call_corr_3', { symbol: 'SPX' }), {
      fetchImpl: fetchConst(gapPayload),
    })
  ).content
).pairs;
const gaps = { count: gapPairs.length };

// ── 3. 不给 symbol：alerts（无 series）+ strongest（不含对角线） ─────────
const overview = JSON.parse(
  (
    await tools.runToolCall(named('query_correlation', 'call_corr_4', {}), {
      fetchImpl: fetchConst(baseCross),
    })
  ).content
);
const overviewOut = {
  alertCount: overview.alerts.length,
  alertKeys: Object.keys(overview.alerts[0]).sort(),
  alertFirst: overview.alerts[0],
  hasSeries: overview.alerts.some((a) =>
    Object.prototype.hasOwnProperty.call(a, 'series')
  ),
  strongestCount: overview.strongest.length,
  strongest: overview.strongest,
  noDiagonal: overview.strongest.every((p) => p.a !== p.b),
  uniquePairs:
    new Set(overview.strongest.map((p) => [p.a, p.b].sort().join('-'))).size ===
    overview.strongest.length,
};

// ── 4. 未知标的：结构化错误 + available 全部 22 个名字 ──────────────────
const allNames = [
  'SPX', 'NDX', 'RUT', 'DJI', 'SOX', 'TLT', 'IEF', 'HYG', 'LQD', 'KBWB', 'Gold',
  'Silver', 'WTI', 'Brent', 'Copper', 'NG', 'BTC', 'ETH', 'DXY', 'USDJPY',
  'EURUSD', 'MOVE',
];
const bigAssets = allNames.map((name) => ({ name, group: 'equity', label: name }));
const bigMatrix = allNames.map((_, i) => allNames.map((_, j) => (i === j ? 1.0 : 0.1)));
const bigCross = {
  as_of: '2026-10-18',
  window: 30,
  assets: bigAssets,
  matrix: bigMatrix,
  alerts: [],
};
const unknownCorrRun = await tools.runToolCall(
  named('query_correlation', 'call_corr_5', { symbol: 'AAPL' }),
  { fetchImpl: fetchConst(bigCross) }
);
const unknownCorr = { ok: unknownCorrRun.ok, content: JSON.parse(unknownCorrRun.content) };

// 原型链上的名字不能被当成工具（'constructor' 不是 TOOL_HANDLERS 的自有键）
const protoRun = await tools.runToolCall(named('constructor', 'call_proto', {}), {
  fetchImpl: fetchConst(baseCross),
});
const proto = { ok: protoRun.ok, content: JSON.parse(protoRun.content) };

// ── 5. 期权快照压缩：真实形状（含 wall / gex_dist / narrative 三个大字段） ─
const optSymbol = (symbol, spot) => ({
  spot,
  net_gex: -1.46,
  net_dex: 87.81,
  call_dex: 26.9,
  put_dex: 60.92,
  pcr_oi: 2.65,
  pcr_oi_atm: 1.61,
  pcr_vol: 1.72,
  gamma_flip: 758.4,
  flip_dist: -0.64,
  call_wall: { strike: 770.0, per_point_m: 70.7 },
  put_wall: { strike: 760.0, per_point_m: -206.5 },
  iv_front: { dte: 0, iv: 13.06 },
  iv_back: { dte: 333, iv: 22.67 },
  iv_slope: 9.61,
  skew: -1.5,
  skew_dte: 7,
  skew_put_iv: 16.69,
  skew_call_iv: 18.19,
  net_vanna: 0.34,
  net_charm: 0.96,
  charm_near7: 55.3,
  buckets: {
    '0DTE': { gex_pct: 10.0, gex_m: -214.7, contracts: 389927 },
    '本周': { gex_pct: 28.6, gex_m: -482.6, contracts: 1054584 },
    '月度': { gex_pct: 55.0, gex_m: -745.9, contracts: 4933421 },
    '季度+': { gex_pct: 6.4, gex_m: -13.4, contracts: 1190258 },
  },
  wall: Array.from({ length: 300 }, (_, i) => ({
    strike: 50 + i,
    gex_m: -0.0,
    call_gex_m: 0.0,
    put_gex_m: -0.0,
    oi: 10653,
  })),
  iv_curve: Array.from({ length: 17 }, (_, i) => ({
    expiration: '2026' + String(1019 + i),
    dte: i,
    iv: 13.06 + i,
  })),
  oi_top: Array.from({ length: 20 }, (_, i) => ({
    strike: 760 + i,
    expiration: '2026-10-30',
    call_oi: 3702 + i,
    put_oi: 75524,
    total: 79226,
    call_vol: 988,
    put_vol: 7505,
  })),
  gex_dist: Array.from({ length: 300 }, (_, i) => ({ strike: 50 + i, gex_m: -0.0 })),
  narrative: {
    gamma: { title: '负 Gamma：波动更容易放大', text: 'x'.repeat(1200) },
    charm: { title: 'Charm 偏空', text: 'y'.repeat(800) },
  },
  symbol,
  name: symbol,
});
const optionsPayload = {
  date: '2026-10-18T23:05:17Z',
  symbols: { SPY: optSymbol('SPY', 763.25), QQQ: optSymbol('QQQ', 612.4) },
};
const optRun = await tools.runToolCall(
  named('query_options_wall', 'call_opt_1', { symbol: 'spy' }),
  { fetchImpl: fetchConst(optionsPayload) }
);
const opt = JSON.parse(optRun.content);
const requiredFields = [
  'symbol', 'name', 'date', 'spot', 'net_gex', 'net_dex', 'pcr_oi', 'pcr_oi_atm',
  'gamma_flip', 'flip_dist', 'call_wall', 'put_wall', 'iv_front', 'iv_back',
  'iv_slope', 'skew', 'skew_dte', 'net_vanna', 'net_charm', 'charm_near7', 'buckets',
];
const options = {
  ok: optRun.ok,
  endpoint: optRun.endpoint,
  symbol: opt.symbol,
  name: opt.name,
  date: opt.date,
  spot: opt.spot,
  netGex: opt.net_gex,
  netDex: opt.net_dex,
  callWall: opt.call_wall.strike,
  putWall: opt.put_wall.strike,
  pcr: opt.pcr_oi,
  missing: requiredFields.filter((k) => !(k in opt)),
  oiTop: opt.oi_top.length,
  ivCurve: opt.iv_curve.length,
  hasWallKey: Object.prototype.hasOwnProperty.call(opt, 'wall'),
  hasGexDist: optRun.content.indexOf('gex_dist') >= 0,
  hasNarrative: optRun.content.indexOf('narrative') >= 0,
  len: optRun.content.length,
  maxChars: tools.TOOL_MAX_CHARS,
  isSummary: Object.prototype.hasOwnProperty.call(opt, '_note'),
};

const unknownOptRun = await tools.runToolCall(
  named('query_options_wall', 'call_opt_2', { symbol: 'NOPE' }),
  { fetchImpl: fetchConst(optionsPayload) }
);
const unknownOpt = { ok: unknownOptRun.ok, content: JSON.parse(unknownOptRun.content) };

// ── 5b. 数组字段排序口径：oi_top 按 total 降序、iv_curve 按 dte 升序 ─────
// 真实端点的 oi_top 22/22 个标的都不是降序（SPY 最大值在第 4 条、AAPL 在第 17 条），
// 「谁排前面取谁」会漏掉 OI 最大的行权价。
const unsortedOpt = optSymbol('AAA', 100);
unsortedOpt.oi_top = [10, 300, 50, 900, 20, 700].map((total, i) => ({
  strike: 100 + i,
  expiration: '2026-10-30',
  total,
}));
unsortedOpt.iv_curve = [
  { expiration: '20261120', dte: 30, iv: 20 },
  { expiration: '20261023', dte: 2, iv: 15 },
  { expiration: '20261025', dte: 9, iv: 16 },
  { expiration: '20261021', dte: 1, iv: 14 },
];
// 缺 total / total 非数字 / 同值：缺的排后面、同值保持原相对顺序
const messyOpt = optSymbol('BBB', 50);
messyOpt.oi_top = [
  { strike: 1, total: 40 },
  { strike: 2 },
  { strike: 3, total: 500 },
  { strike: 4, total: null },
  { strike: 5, total: 500 },
];
messyOpt.iv_curve = [
  { expiration: '20261120', dte: 30, iv: 20 },
  { expiration: '20261021', dte: null, iv: 14 },
  { expiration: '20261025', dte: 9, iv: 16 },
];
const sortedPayload = {
  date: '2026-10-18T23:05:17Z',
  symbols: { AAA: unsortedOpt, BBB: messyOpt },
};
const sortedRun = await tools.runToolCall(
  named('query_options_wall', 'call_opt_sort', { symbol: 'AAA' }),
  { fetchImpl: fetchConst(sortedPayload) }
);
const messyRun = await tools.runToolCall(
  named('query_options_wall', 'call_opt_messy', { symbol: 'BBB' }),
  { fetchImpl: fetchConst(sortedPayload) }
);
const sortedOpt = JSON.parse(sortedRun.content);
const messy = JSON.parse(messyRun.content);
const ordering = {
  oiTotals: sortedOpt.oi_top.map((r) => r.total),
  oiCount: sortedOpt.oi_top.length,
  ivDtes: sortedOpt.iv_curve.map((r) => r.dte),
  ivCount: sortedOpt.iv_curve.length,
  messyOk: messyRun.ok,
  messyTotals: messy.oi_top.map((r) => (r.total === undefined ? 'missing' : r.total)),
  messyStrikes: messy.oi_top.map((r) => r.strike),
  messyIvDtes: messy.iv_curve.map((r) => r.dte),
};

// ── 6. 共用管线：新工具同样受单次超时 / 404 归形约束 ─────────────────────
const timeoutStarted = Date.now();
const timedCorr = await tools.runToolCall(
  named('query_correlation', 'call_timeout', { symbol: 'SPX' }),
  {
    deadline: Date.now() + 500,
    callTimeoutMs: 60, // 测试覆写：生产是 3.5s
    fetchImpl: () => new Promise(() => {}),
  }
);
const timeout = {
  ok: timedCorr.ok,
  endpoint: timedCorr.endpoint,
  error: JSON.parse(timedCorr.content),
  elapsedMs: Date.now() - timeoutStarted,
};
const notFoundRun = await tools.runToolCall(
  named('query_options_wall', 'call_nf', { symbol: 'SPY' }),
  { fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }) }
);
const notFound = { ok: notFoundRun.ok, content: JSON.parse(notFoundRun.content) };

// ── 7. 缓存：键带工具名；同工具重复命中、两个工具各自独立 ────────────────
const cache = new Map();
const urls = [];
const fetchBoth = async (url) => {
  urls.push(url);
  return {
    ok: true,
    status: 200,
    json: async () => (url.indexOf('cross-asset') >= 0 ? baseCross : optionsPayload),
  };
};
const cacheFirst = await tools.runToolCall(
  named('query_correlation', 'call_cache_1', { symbol: 'NDX' }),
  { fetchImpl: fetchBoth, cache }
);
const cacheSecond = await tools.runToolCall(
  named('query_correlation', 'call_cache_2', { symbol: 'ndx' }),
  { fetchImpl: fetchBoth, cache }
);
const cacheWall = await tools.runToolCall(
  named('query_options_wall', 'call_cache_3', { symbol: 'SPY' }),
  { fetchImpl: fetchBoth, cache }
);
const cacheNoSymbol = await tools.runToolCall(
  named('query_correlation', 'call_cache_4', {}),
  { fetchImpl: fetchBoth, cache }
);
const cacheBlankSymbol = await tools.runToolCall(
  named('query_correlation', 'call_cache_5', { symbol: '   ' }),
  { fetchImpl: fetchBoth, cache }
);
const cacheOut = {
  urls,
  first: { callId: cacheFirst.callId, content: cacheFirst.content },
  second: {
    callId: cacheSecond.callId,
    content: cacheSecond.content,
    cached: cacheSecond.cached,
    ok: cacheSecond.ok,
  },
  wall: { ok: cacheWall.ok, content: cacheWall.content, cached: cacheWall.cached },
  noSymbol: { cached: cacheNoSymbol.cached },
  blankSymbol: { cached: cacheBlankSymbol.cached },
  keys: {
    corr: tools.toolCacheKey('query_correlation', 'cross-asset', 'spx'),
    site: tools.toolCacheKey('query_site_data', 'cross-asset', 'spx'),
    wall: tools.toolCacheKey('query_options_wall', 'assets/equities-options', 'spy'),
    emptyVariant: tools.toolCacheKey('query_correlation', 'cross-asset'),
  },
};

// ── 8. 提示词：一句话分工（三个工具名都出现，且不是三段） ────────────────
const promptOut = {
  division: prompt.AI_TOOL_DIVISION,
  inSystem: prompt.AI_SYSTEM_PROMPT.indexOf(prompt.AI_TOOL_DIVISION) >= 0,
  count: prompt.AI_SYSTEM_PROMPT.split(prompt.AI_TOOL_DIVISION).length - 1,
  oneLine: prompt.AI_TOOL_DIVISION.indexOf('\\n') < 0,
  names: ['query_site_data', 'query_correlation', 'query_options_wall'].map(
    (n) => prompt.AI_SYSTEM_PROMPT.indexOf(n) >= 0
  ),
};

console.log(
  JSON.stringify({
    connect,
    truncation,
    gaps,
    overview: overviewOut,
    unknownCorr,
    proto,
    options,
    unknownOpt,
    ordering,
    timeout,
    notFound,
    cache: cacheOut,
    prompt: promptOut,
  })
);
"""


@lru_cache(maxsize=1)
def _run_t4() -> dict:
    script = T4_BODY.replace("__TOOLS__", TOOLS.as_uri()).replace("__PROMPT__", PROMPT.as_uri())
    return run_node(script, timeout=60)


def test_tool_catalog_schema() -> None:
    out = _run()["catalog"]
    # T4 起三个工具：query_site_data + 两个跨端点工具（服务端上限 8 个，还够）
    assert out["count"] == 3
    assert out["count"] <= 8
    assert out["names"] == ["query_site_data", "query_correlation", "query_options_wall"]
    assert out["types"] == ["function", "function", "function"]
    assert out["type"] == "function"
    assert out["name"] == "query_site_data"
    assert out["requiredByTool"] == [["endpoint"], [], ["symbol"]]
    assert out["propsByTool"] == [["endpoint", "json_path"], ["symbol"], ["symbol"]]
    assert out["enum"] == EXPECTED_PATHS
    assert out["required"] == ["endpoint"]
    assert out["jsonPathType"] == "string"
    assert out["jsonPathRequired"] is False
    assert out["descHasPath"] is True
    assert out["descLen"] > 0
    # 服务端（functions/_lib/common.js 与 src/chat_proxy.py）都卡 500 字上限：
    # 17 个端点的说明只能进提示词索引，不能塞进 tool description；三个工具都一样。
    assert all(0 < n <= 500 for n in out["descLens"]), out["descLens"]
    assert out["maxChars"] == 2000
    assert out["maxCalls"] == 3
    assert out["maxRounds"] == 2
    assert out["timeout"] == 8000
    assert out["byName"] is True
    assert out["byNameAll"] is True
    assert out["byNameMissing"] is True


def test_catalog_paths_are_unique_and_grouped() -> None:
    out = _run()["catalogMeta"]
    entries = out["entries"]
    assert out["count"] == 17 == len(entries)
    paths = [e["path"] for e in entries]
    assert paths == EXPECTED_PATHS
    assert len(set(paths)) == 17, "端点路径重复"
    bad = [p for p in paths if not PATH_RE.match(p)]
    assert not bad, f"端点路径格式应为 group/name（小写字母数字连字符）：{bad}"
    groups = {e["group"] for e in entries}
    assert groups == {"rates", "credit", "liquidity", "volatility"}
    # 白名单 / enum 都必须从目录派生，不能各写一份
    assert out["endpointsMatch"] is True
    assert out["enumMatch"] is True
    # 830KB–2MB 的区间图表数据不进目录（白名单只暴露稳定读数）
    assert not any("overview_" in p for p in paths)


def test_every_catalog_path_has_an_exporter() -> None:
    """每条路径都要在 src/export_pages.py 里有对应导出（干净 clone 没有 public/api）。

    两种写法：
    - 显式字符串：`_safe("api/credit/stress", ...)` → 断言 `api/<path>` 出现在源码；
    - rates 族元组表：`("yield-curve", get_rates_yield_curve)` → 断言最后一段以
      `("<name>"` 出现在那张表的区间内。
    """
    src = EXPORT_PAGES.read_text(encoding="utf-8")
    start = src.find("for name, fn in [")
    assert start != -1, "export_pages.py 的 rates 元组表不见了，测试需要同步"
    end = src.find("]:", start)
    assert end != -1, "rates 元组表没有闭合"
    rates_block = src[start:end]
    entries = _run()["catalogMeta"]["entries"]
    missing = []
    reasons = {}
    for e in entries:
        path = e["path"]
        if f"api/{path}" in src:
            reasons[path] = "explicit"
            continue
        name = path.rsplit("/", 1)[-1]
        if path.startswith("rates/") and f'("{name}"' in rates_block:
            reasons[path] = "rates-tuple"
            continue
        missing.append(path)
    assert not missing, f"目录里有端点没有导出实现：{missing}"
    # 守住测试本身：rates 族必须真的走元组表分支，而不是被显式字符串兜住
    rates = [p for p in EXPECTED_PATHS if p.startswith("rates/")]
    assert rates and all(reasons.get(p) == "rates-tuple" for p in rates)


def test_catalog_legend_feeds_prompt_without_hardcoding() -> None:
    out = _run()
    meta = out["catalogMeta"]
    index = out["promptIndex"]
    entries = meta["entries"]
    legend = meta["legend"]
    lines = legend.splitlines()
    assert len(lines) == 4, f"应按 group 每个一行，实际 {len(lines)} 行：{legend!r}"
    for group in ("rates", "credit", "liquidity", "volatility"):
        assert any(line.startswith(group + ": ") for line in lines), f"索引缺 {group} 族"
    for e in entries:
        name = e["path"].rsplit("/", 1)[-1]
        assert name + "=" in legend, f"索引缺端点 {e['path']}"
        assert e["label"] in legend, f"索引缺 {e['path']} 的中文含义"
    # 提示词里确实嵌入了整段索引，而不是另写一份
    assert index["inSystem"] is True
    assert index["hasLegend"] is True
    assert index["fallbackHint"] is True
    # 标题 + 四族各一行 + 换路提示 = 6 行
    assert index["lines"] == 6
    # 反向守卫：ai-prompt.js 不许出现任何端点路径字面量（必须由函数生成）
    prompt_src = PROMPT.read_text(encoding="utf-8")
    assert "toolCatalogLegend(" in prompt_src, "提示词没走 toolCatalogLegend() 生成索引"
    hardcoded = [p for p in EXPECTED_PATHS if p in prompt_src]
    assert not hardcoded, f"ai-prompt.js 手抄了端点路径：{hardcoded}"
    # 连「端点名=含义」这种索引条目也不许抄：必须 run-time 由 toolCatalogLegend() 生成
    copied = []
    for e in entries:
        name = e["path"].rsplit("/", 1)[-1]
        if name + "=" in prompt_src or e["label"] in prompt_src:
            copied.append(e["path"])
    assert not copied, f"ai-prompt.js 手抄了索引条目：{copied}"


def test_large_endpoint_returns_structured_summary() -> None:
    """80KB 端点不能硬截前 2000 字：要给结构摘要 + json_path 下钻提示。"""
    out = _run()["bigEndpoint"]
    assert out["rawLen"] > 80000, f"测试载荷不够大：{out['rawLen']}"
    assert out["endpoint"] == "liquidity/transmission-chain"
    assert out["len"] <= 2000
    assert out["total"] == out["rawLen"]
    assert "json_path" in out["note"]
    assert out["asOf"] == "2026-10-21"
    assert out["seriesType"].startswith("array[1800]")
    assert "date" in out["seriesType"]


def test_json_path_drilldown() -> None:
    out = _run()["drill"]
    assert out["deep"] == {"found": True, "value": 4.812, "available": []}
    assert out["index"]["found"] is True
    assert out["index"]["value"] == 2
    assert out["empty"]["found"] is True
    assert out["empty"]["value"]["as_of"] == "2026-10-21"
    assert out["badIndex"]["found"] is False
    assert "array 长度 2" in out["badIndex"]["available"][0]
    assert out["badKey"]["found"] is False
    assert out["badKey"]["available"] == ["current", "series"]
    assert out["throughNull"]["found"] is False


def test_truncation_and_volume_strategy() -> None:
    out = _run()["volume"]
    assert out["smallUntouched"] is True
    # 大对象给结构摘要，不是前 2000 字的半截 JSON
    assert out["summaryLen"] <= 2000
    assert out["summaryTotal"] == out["summaryRawLen"]
    assert "json_path" in out["summaryNote"]
    assert out["summaryAsOf"] == "2026-10-21"
    assert out["summaryArrayType"].startswith("array[365]")
    assert "date" in out["summaryArrayType"]
    assert "value" in out["summaryArrayType"]
    assert out["summaryObjectType"].startswith("object[2]")
    assert "b:array[3]" in out["summaryObjectType"]
    assert out["summaryNoteTruncated"] is True
    # 长数组给首尾采样 + 总长度
    assert out["arrayLen"] <= 2000
    assert out["arrayTotal"] == 365
    assert out["arrayFirst"] == 3
    assert out["arrayLast"] == 3
    assert out["arrayFirstValue"] == 0
    assert out["arrayLastValue"] == 364
    assert "json_path" in out["arrayNote"]
    # 超长标量仍是合法 JSON 字符串，且带截断标注
    assert out["hugeLen"] <= 2000
    assert out["hugeParses"] is True
    assert out["hugeHasMarker"] is True
    assert out["truncLen"] <= 2000
    assert out["truncMarker"] is True
    assert out["exactUntouched"] is True


def test_executor_shapes_failures_without_throwing() -> None:
    out = _run()["execute"]
    # 成功：静态站端点无 .json 后缀，下钻后原样返回小结果
    assert out["seenUrl"] == "/api/rates/real-rates"
    assert out["okEndpoint"] == "rates/real-rates"
    assert out["okContent"] == "2026-10-21"
    # 404 可合法发生：结构化结果里没有数字可编
    nf = out["nf"]
    assert sorted(nf) == ["endpoint", "error", "status"]
    assert nf["endpoint"] == "rates/yield-curve"
    assert nf["status"] == 404
    assert "未导出" in nf["error"]
    # 断网 / 超时：status 0，同一句话术由模型转述
    assert out["networkError"]["status"] == 0
    assert "网络" in out["networkError"]["error"]
    assert out["timeoutError"]["status"] == 0
    assert "超时" in out["timeoutError"]["error"] or "取消" in out["timeoutError"]["error"]
    # 白名单外端点不发起请求（含目录外但形似真端点的路径）
    assert out["evilCalled"] is False
    assert "白名单" in out["evil"]["error"]
    assert out["overviewCalled"] is False
    assert "白名单" in out["overview"]["error"]
    # 路径不存在时带该层可用 key，模型第二次能改
    missing = out["missing"]
    assert missing["available"] == ["a", "b"]
    assert "JSON 路径不存在" in missing["error"]
    # 坏参数 / 未知工具同样收敛
    assert "JSON" in out["badJson"]["error"]
    assert "未知工具" in out["unknown"]["error"]


def test_network_retry_is_bounded() -> None:
    out = _run()["retry"]
    # 第一次网络层失败、第二次成功：真的重试了一次并返回成功结果
    assert out["okCount"] == 2
    assert out["okContent"] == {"value": 5.43}
    # 剩余预算不足 2s：只调一次，不重试
    assert out["shortCount"] == 1
    assert out["shortError"]["status"] == 0
    assert "已重试一次" not in out["shortError"]["error"]
    # 用户主动停止：不重试，文案明确是取消
    assert out["abortCount"] == 1
    assert "已取消" in out["abortError"]["error"]
    # 两次都失败：两次调用 + 「已重试一次」
    assert out["failCount"] == 2
    assert "已重试一次" in out["failError"]["error"]
    # 纯函数边界：只第一次、只 status 0、非 abort、剩余 ≥2s
    assert out["can"] == {
        "first": True,
        "second": False,
        "userAbort": False,
        "signalAbort": False,
        "short": False,
        "notNetwork": False,
    }
    assert out["budgetDeadline"] is True
    assert out["budgetMs"] is True
    assert out["budgetNone"] == 0


def test_sse_fragments_rebuild_arguments() -> None:
    out = _run()["sse"]
    assert out["done"] == "[DONE]"
    assert out["nonData"] is None
    assert out["broken"] == {
        "content": "",
        "reasoning": "",
        "toolCalls": [],
        "finishReason": "",
    }
    assert out["finalCount"] == 1
    assert out["id"] == "call_a"
    assert out["name"] == "query_site_data"
    assert out["args"] == {
        "endpoint": "rates/yield-curve",
        "json_path": "yield_curve.spreads",
    }
    assert out["finish"] == "tool_calls"
    # 首片缺 arguments 不能被 safeStringify 成 "undefined" 拼进 JSON
    assert out["missingArgs"] == ""
    # 多调用交错到达时按 index 排序，id 不串
    assert out["order"] == ["call_a", "call_b"]
    assert out["orderArgs"] == ["rates/yield-curve", "volatility/dashboard"]


def test_tool_round_limits() -> None:
    out = _run()["limits"]
    assert out["first"]["round"] == 1
    assert [c["id"] for c in out["first"]["calls"]] == ["c1", "c2", "c3"]
    assert out["first"]["dropped"] == 1
    assert out["second"]["round"] == 2
    assert out["exhausted"] is None
    assert out["empty"] is None
    assert [c["id"] for c in out["custom"]["calls"]] == ["c1"]
    assert out["custom"]["dropped"] == 3
    # 预算内继续 auto；用尽且仍有 calls → none（runTurn 据此强制开口）
    choices = out["choices"]
    assert choices["start"] == "auto"
    assert choices["mid"] == "auto"
    assert choices["exhausted"] == "none"
    assert choices["noCalls"] == "auto"
    assert choices["over"] == "none"
    assert choices["custom"] == "none"
    # 循环控制：预算内继续查；rounds=2 仍有 calls → force + tool_choice none；
    # 已经强制过一轮则必须 stop（防无限循环）
    loop = out["loop"]
    assert loop["tools"]["kind"] == "tools"
    assert loop["mid"]["kind"] == "tools"
    assert loop["force"]["kind"] == "force"
    assert loop["force"]["toolChoice"] == "none"
    assert loop["stop"]["kind"] == "stop"
    assert loop["afterForce"]["kind"] == "stop"


def test_object_array_summary_exposes_element_keys() -> None:
    out = _run()["describe"]
    assert out["objectArray"].startswith("array[2] of object{")
    assert "tenor" in out["objectArray"]
    assert "yield" in out["objectArray"]
    assert "change" in out["objectArray"]
    assert out["primitiveArray"] == "array[3]"
    assert out["emptyArray"] == "array[0]"


def test_reasoning_reject_fallback_helpers() -> None:
    out = _run()["degrade"]
    assert out["hit"] is True
    assert out["spaced"] is True
    assert out["other400"] is False
    assert out["wrongStatus"] is False
    # 降级只留 system + 最后一条 user，tool / assistant 历史全部丢掉
    assert out["minimalRoles"] == ["system", "user"]
    assert out["minimalLast"] == "u2"
    assert out["minimalEmpty"] == []


def test_prompt_and_payload_wire_tools() -> None:
    out = _run()["wiring"]
    assert out["version"] == "2026-10-22"
    assert "query_site_data" in out["system"]
    assert "只依据当前页面" not in out["system"]
    assert out["hasBudget"] is True
    assert "query_site_data" in out["factsHint"]
    # 代理与直连都带工具目录；代理仍不传 reasoning_effort（单源规则没被破坏）
    assert out["proxyTool"] == "query_site_data"
    assert out["proxyChoice"] == "auto"
    assert out["proxyEffort"] is False
    assert out["directTool"] == "query_site_data"
    assert out["directChoice"] == "auto"
    assert out["directEffort"] == "high"
    assert out["forcedChoice"] == "none"


# ── T3：失败与降级 ────────────────────────────────────────────────────────


def test_t3_limits_keep_other_budgets() -> None:
    """新增单次 3.5s 上限，但 8s / 2000 字 / 3 次 / 2 轮一个都不许动。"""
    out = _run_t3()["constants"]
    assert out["callTimeout"] == 3500
    assert out["roundBudget"] == 8000
    assert out["maxChars"] == 2000
    assert out["maxCalls"] == 3
    assert out["maxRounds"] == 2


def test_single_attempt_timeout_is_bounded() -> None:
    """一次取数卡死只烧掉单次上限，不吃光整轮 8s（这里是 60ms 的测试覆写）。"""
    out = _run_t3()["timeout"]
    err = out["error"]
    assert out["ok"] is False
    assert "单次" in err["error"]
    assert "已重试一次" not in err["error"]
    assert err["status"] == 0
    assert err["endpoint"] == "rates/yield-curve"
    # 永不 resolve 的 fetchImpl 也必须被计时器掐断：一次尝试 ≈60ms，
    # 若退回「等批次 8s」这里会是 ~8s（断言 1s 留足 CI 抖动余量）。
    assert out["attempts"] == 1
    assert out["elapsedMs"] < 1000


def test_two_single_timeouts_retry_once() -> None:
    """剩余预算 ≥2s：单次超时后再试一次，两次都超时才落成错误。"""
    out = _run_t3()["timeoutTwice"]
    err = out["error"]
    assert out["ok"] is False
    assert out["attempts"] == 2
    assert "单次" in err["error"]
    assert "已重试一次" in err["error"]
    assert err["status"] == 0
    assert out["elapsedMs"] < 1000


def test_single_timeout_then_retry_succeeds() -> None:
    """单次超时后通常还剩 ~4.5s：重试规则能再起一次并救回来。"""
    out = _run_t3()["rescue"]
    assert out["attempts"] == 2
    assert out["ok"] is True
    assert out["content"] == {"value": 5.43}
    assert out["elapsedMs"] < 1000


def test_same_turn_cache_hits_once() -> None:
    """endpoint+json_path 同轮只取一次；换路径 / 换 Map 都不命中。"""
    out = _run_t3()["cache"]
    # 第一次 + 换 json_path + 换新 Map = 3 次请求；第二次命中不请求
    assert out["fetches"] == 3
    assert out["first"]["callId"] == "call_cache_1"
    # callId 必须是本次调用自己的，tool_call_id 才能对上
    assert out["second"]["callId"] == "call_cache_2"
    assert out["second"]["cached"] is True
    assert out["second"]["ok"] is True
    assert out["second"]["content"] == out["first"]["content"]
    # 换 json_path 是另一个键：真的发了请求，且拿到该层的「路径不存在」错误
    assert out["otherPath"].get("cached") is None
    assert out["otherPath"]["ok"] is False
    assert "JSON 路径不存在" in out["otherPath"]["content"]["error"]
    # 换新 Map（= 新的一轮）不命中，但内容与首轮一致
    assert out["newTurn"].get("cached") is None
    assert out["newTurn"]["ok"] is True
    assert out["newTurn"]["content"] == out["first"]["content"]


def test_concurrent_same_call_fetches_once() -> None:
    """同一批 Promise.all 里两条相同调用：先占位再 await，真实网络只命中 1 次。"""
    out = _run_t3()["concurrent"]
    assert out["fetches"] == 1
    # 两个 tool_call_id 分别对得上（不能用被缓存那条的 id 顶替）
    assert out["a"]["callId"] == "call_dup_a"
    assert out["b"]["callId"] == "call_dup_b"
    assert out["a"]["content"] == out["b"]["content"]
    # 后到的那条命中的是在途 promise；发起的那条不算「命中缓存」
    assert out["a"].get("cached") is None
    assert out["b"]["cached"] is True


def test_failed_results_are_cached_too() -> None:
    """失败也缓存：同一轮里同端点结果不会变，404 只打一次。"""
    out = _run_t3()["failCache"]
    assert out["fetches"] == 1
    assert out["first"]["ok"] is False
    assert out["second"]["ok"] is False
    assert out["second"]["cached"] is True
    assert out["first"]["content"] == out["second"]["content"]
    assert "未导出" in out["second"]["content"]


def test_ok_flag_marks_structured_errors() -> None:
    """200 → ok true；404 → ok false（UI/msg.toolsFailed 靠它分流）。"""
    assert _run_t3()["okFlag"] == {"success": True, "notFound": False}


def test_summarize_tool_runs_splits_endpoints() -> None:
    """msg.tools / msg.toolsFailed 的单源纯函数：去重、保序、成功优先。"""
    out = _run_t3()["summarize"]
    assert out["mixed"]["tools"] == ["rates/yield-curve", "liquidity/snapshot"]
    assert out["mixed"]["toolsFailed"] == ["credit/stress", "credit/cds"]
    # 同一端点既有成功又有失败（不同 json_path）：能取到就算成功，不重复标「未取到」
    assert out["both"] == {"tools": ["rates/auctions"], "toolsFailed": []}
    assert out["empty"] == {"tools": [], "toolsFailed": []}


def test_chat_wires_turn_cache_and_failed_meta() -> None:
    """ai-chat.js 的接线守卫：缓存 Map 必须建在 runTurn 函数体内（跨轮清空）。"""
    src = (JS / "ai-chat.js").read_text(encoding="utf-8")
    run_turn = src.index("async function runTurn")
    cache_decl = src.index("const toolCache = new Map();")
    assert cache_decl > run_turn, "toolCache 必须在 runTurn 内新建，不能是模块级变量"
    assert src.count("const toolCache = new Map();") == 1
    # 透传给 runToolBatch → runToolCall，而不是在执行器里另建
    assert "turn.signal,\n          toolCache" in src
    assert "summarizeToolRuns(toolRuns)" in src
    # msg.tools 形状不变（字符串数组），失败端点进新字段；渲染为灰字「未取到」
    assert "msg.tools = runSummary.tools;" in src
    assert "msg.toolsFailed = runSummary.toolsFailed;" in src
    assert "'未取到 ' + list.join(' / ')" in src


# ── T4：两个跨端点工具 ────────────────────────────────────────────────────


def test_correlation_connects_names_not_indices() -> None:
    """3×3 假矩阵：pairs 里必须是标的代码 + 对得上的系数，不是矩阵下标。"""
    out = _run_t4()["connect"]
    assert out["ok"] is True
    assert out["endpoint"] == "cross-asset"
    # 小写输入按端点目录回填规范名
    assert out["symbol"] == "NDX"
    assert out["label"] == "纳指100"
    assert out["group"] == "equity"
    assert out["asOf"] == "2026-10-18"
    assert out["window"] == 30
    assert out["pairNames"] == ["SPX", "TLT"]
    assert out["pairLabels"] == ["标普500", "长期国债"]
    assert out["pairCorrs"] == [0.92, 0.05]
    assert out["allNamesAreTickers"] is True
    assert out["selfExcluded"] is True
    # 裸矩阵不许透传（模型读不懂 22×22 的浮点表，也对不上标的）
    assert out["noRawMatrix"] is True


def test_correlation_sorts_and_truncates_pairs() -> None:
    """|corr| 降序、最多 8 条、排除自身；null / NaN 的格子跳过。"""
    out = _run_t4()["truncation"]
    assert out["count"] == 8
    assert out["names"] == ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
    assert out["corrs"] == [-0.95, 0.91, -0.87, 0.83, -0.79, 0.75, -0.71, 0.67]
    assert out["selfExcluded"] is True
    assert out["droppedWeakest"] is True
    assert out["sortedDesc"] is True
    assert _run_t4()["gaps"]["count"] == 0


def test_correlation_overview_alerts_and_strongest() -> None:
    """不给 symbol：4 条结构报警（去掉 67 点 series）+ 最强 5 对（不含对角线）。"""
    out = _run_t4()["overview"]
    assert out["alertCount"] == 4
    assert out["alertKeys"] == ["key", "label", "latest", "prev"]
    assert out["alertFirst"]["key"] == "SPX_TLT_30d"
    assert out["alertFirst"]["latest"] == 0.2151
    assert out["alertFirst"]["prev"] == 0.201
    assert out["hasSeries"] is False
    assert out["strongestCount"] == 3
    assert out["strongest"][0] == {"a": "SPX", "b": "NDX", "corr": 0.92}
    assert out["noDiagonal"] is True
    assert out["uniquePairs"] is True


def test_correlation_unknown_symbol_lists_available() -> None:
    """未知标的 → 结构化错误（status 0）+ available 全部 22 个名字。"""
    out = _run_t4()["unknownCorr"]
    assert out["ok"] is False
    content = out["content"]
    assert content["error"] == "未知标的：AAPL"
    assert content["endpoint"] == "cross-asset"
    assert content["status"] == 0
    assert len(content["available"]) == 22
    assert content["available"][0] == "SPX"
    assert "MOVE" in content["available"]
    assert "AAPL" not in content["available"]
    # 原型链上的名字不是工具（分派表用 hasOwnProperty 守卫）
    proto = _run_t4()["proto"]
    assert proto["ok"] is False
    assert "未知工具" in proto["content"]["error"]


def test_options_summary_is_compressed() -> None:
    """真实形状的期权快照 → 21 个结构字段 + oi_top 5 + iv_curve 3，丢掉三个大字段。"""
    out = _run_t4()["options"]
    assert out["ok"] is True
    assert out["endpoint"] == "assets/equities-options"
    assert out["symbol"] == "SPY"
    assert out["name"] == "SPY"
    assert out["date"] == "2026-10-18T23:05:17Z"
    assert out["spot"] == 763.25
    assert out["netGex"] == -1.46
    assert out["netDex"] == 87.81
    assert out["callWall"] == 770.0
    assert out["putWall"] == 760.0
    assert out["pcr"] == 2.65
    assert out["missing"] == []
    assert out["oiTop"] == 5
    assert out["ivCurve"] == 3
    # 三个大字段必须丢：wall 逐行权、gex_dist 分布、narrative 散文
    assert out["hasWallKey"] is False
    assert out["hasGexDist"] is False
    assert out["hasNarrative"] is False
    # 压缩后直接给 JSON，不触发结构摘要
    assert out["isSummary"] is False
    assert out["maxChars"] == 2000
    assert out["len"] <= 2000


def test_options_oi_top_sorted_by_total() -> None:
    """oi_top 端点给的不是降序：按 total 降序取前 5；缺 total 不崩且排后面。"""
    out = _run_t4()["ordering"]
    assert out["oiCount"] == 5
    assert out["oiTotals"] == [900, 700, 300, 50, 20]
    assert 10 not in out["oiTotals"], "最小值 10 不该挤掉 900/700"
    # iv_curve 按 dte 升序（最近的到期在前）
    assert out["ivCount"] == 3
    assert out["ivDtes"] == [1, 2, 9]
    # total 缺失 / null 不崩：缺的排后面，同值保持原相对顺序（strike 3 在 5 前）
    assert out["messyOk"] is True
    assert out["messyTotals"] == [500, 500, 40, "missing", None]
    assert out["messyStrikes"] == [3, 5, 1, 2, 4]
    # dte 缺失同样排后面
    assert out["messyIvDtes"] == [9, 30, None]


def test_options_unknown_symbol_lists_available() -> None:
    out = _run_t4()["unknownOpt"]
    assert out["ok"] is False
    content = out["content"]
    assert content["error"] == "未知标的：NOPE"
    assert content["endpoint"] == "assets/equities-options"
    assert content["status"] == 0
    assert content["available"] == ["SPY", "QQQ"]


def test_new_tools_share_executor_pipeline() -> None:
    """新工具不另写执行器：单次超时与 404 归形都走同一套（T3 行为不退）。"""
    out = _run_t4()
    timed = out["timeout"]
    assert timed["ok"] is False
    assert timed["endpoint"] == "cross-asset"
    assert timed["error"]["status"] == 0
    assert "单次" in timed["error"]["error"]
    # 永不 resolve 的 fetch 也只烧掉单次上限（60ms 测试覆写），不吃整轮 8s
    assert timed["elapsedMs"] < 1000
    nf = out["notFound"]
    assert nf["ok"] is False
    assert nf["content"]["endpoint"] == "assets/equities-options"
    assert nf["content"]["status"] == 404
    assert "未导出" in nf["content"]["error"]


def test_t4_cache_keys_include_tool_name() -> None:
    """同轮缓存：同工具同参数只 1 次网络；两个工具各自独立键。"""
    out = _run_t4()["cache"]
    assert out["urls"] == [
        "/api/cross-asset",
        "/api/assets/equities-options",
        "/api/cross-asset",
    ]
    assert out["first"]["callId"] == "call_cache_1"
    # 大小写归一后命中同一键；callId 仍是本次调用自己的
    assert out["second"]["cached"] is True
    assert out["second"]["callId"] == "call_cache_2"
    assert out["second"]["content"] == out["first"]["content"]
    # 另一个工具读另一个端点，不命中相关性那条
    assert out["wall"].get("cached") is None
    assert out["wall"]["ok"] is True
    # 不给 symbol / 空白 symbol 是同一个键（第二次命中）
    assert out["noSymbol"].get("cached") is None
    assert out["blankSymbol"]["cached"] is True
    keys = out["keys"]
    assert keys["corr"] == "query_correlation|cross-asset|spx"
    assert keys["site"] == "query_site_data|cross-asset|spx"
    assert keys["corr"] != keys["site"], "缓存键必须带工具名"
    assert keys["wall"] == "query_options_wall|assets/equities-options|spy"
    assert keys["emptyVariant"] == "query_correlation|cross-asset|"


def test_prompt_states_tool_division_in_one_sentence() -> None:
    """提示词加一句三者分工（工具名可硬编码，端点名仍只能由 legend 生成）。"""
    out = _run_t4()["prompt"]
    assert out["inSystem"] is True
    assert out["count"] == 1, "分工说明只出现一次"
    assert out["oneLine"] is True, "别展开成三段"
    assert out["names"] == [True, True, True]
    sentence = out["division"]
    assert "query_site_data" in sentence
    assert "原始读数" in sentence
    assert "query_correlation" in sentence
    assert "谁跟谁一起动" in sentence
    assert "query_options_wall" in sentence
    assert "期权结构" in sentence
