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
"""

from __future__ import annotations

import re
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


def test_tool_catalog_schema() -> None:
    out = _run()["catalog"]
    assert out["count"] == 1
    assert out["type"] == "function"
    assert out["name"] == "query_site_data"
    assert out["enum"] == EXPECTED_PATHS
    assert out["required"] == ["endpoint"]
    assert out["jsonPathType"] == "string"
    assert out["jsonPathRequired"] is False
    assert out["descHasPath"] is True
    # 服务端（functions/_lib/common.js 与 src/chat_proxy.py）都卡 500 字上限：
    # 17 个端点的说明只能进提示词索引，不能塞进 tool description。
    assert 0 < out["descLen"] <= 500
    assert out["maxChars"] == 2000
    assert out["maxCalls"] == 3
    assert out["maxRounds"] == 2
    assert out["timeout"] == 8000
    assert out["byName"] is True
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
