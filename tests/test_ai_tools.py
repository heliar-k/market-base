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
"""

from __future__ import annotations

from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "frontend" / "public" / "js"
TOOLS = JS / "ai-tools.js"
PROMPT = JS / "ai-prompt.js"
CONFIG = JS / "ai-config.js"

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
  maxChars: tools.TOOL_MAX_CHARS,
  maxCalls: tools.MAX_CALLS_PER_ROUND,
  maxRounds: tools.MAX_TOOL_ROUNDS,
  timeout: tools.TOOL_TIMEOUT_MS,
  byName: !!tools.toolByName('query_site_data'),
  byNameMissing: tools.toolByName('nope') === null,
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

console.log(
  JSON.stringify({
    catalog,
    drill,
    volume,
    execute,
    retry,
    sse,
    limits,
    degrade,
    describe,
    wiring,
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
    assert out["enum"] == [
        "rates/yield-curve",
        "rates/real-rates",
        "volatility/dashboard",
    ]
    assert out["required"] == ["endpoint"]
    assert out["jsonPathType"] == "string"
    assert out["jsonPathRequired"] is False
    assert out["descHasPath"] is True
    assert out["maxChars"] == 2000
    assert out["maxCalls"] == 3
    assert out["maxRounds"] == 2
    assert out["timeout"] == 8000
    assert out["byName"] is True
    assert out["byNameMissing"] is True


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
    # 白名单外端点不发起请求
    assert out["evilCalled"] is False
    assert "白名单" in out["evil"]["error"]
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
