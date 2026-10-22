// ai-tools.js — 伴读抽屉的站内数据工具（纯逻辑，不碰 DOM，可直接用 node 测）。
//
// 为什么单独一个文件：
//   - 工具目录是「具名端点枚举」，模型只能选 enum 里的端点，不能传任意路径
//     （等于给模型一个站内扫描器，见 docs/adr/0006-companion-drawer-tools.md）；
//   - 执行器、JSON 下钻、大结果摘要、SSE 分片累积都是纯函数，抽出来才能 node 直接测；
//   - ai-chat.js 只负责流式循环与渲染，ai-config.js 只把它塞进请求体单源。
//
// 失败一律收敛成 {error, endpoint, status} 的 JSON 文本，绝不抛：
// 静态站端点可合法 404（src/export_pages.py 的 _safe() 跳过当日缺数据的端点），
// 网络断/超时同理，抛异常会让整轮对话断掉。

export const TOOL_NAME = 'query_site_data';

// 具名端点白名单：加端点必须同时改这里与下面 TOOLS 的 enum，且只暴露真正稳定的读数。
export const TOOL_ENDPOINTS = ['rates/yield-curve', 'rates/real-rates', 'volatility/dashboard'];

// 工具目录 v1（与 docs/SPEC-ai-drawer-tools.md「工具目录 v1」逐字一致）。
export const TOOLS = [
  {
    type: 'function',
    function: {
      name: TOOL_NAME,
      description:
        '查询本站已导出的数据端点。用于页面摘要里没有的读数（其他页面、更长历史、其他标的）。返回 JSON 文本。',
      parameters: {
        type: 'object',
        properties: {
          endpoint: {
            type: 'string',
            enum: TOOL_ENDPOINTS.slice(),
            description: '要查询的端点',
          },
          json_path: {
            type: 'string',
            description: '可选的点号路径，如 data.current，只取该子树以节省上下文',
          },
        },
        required: ['endpoint'],
      },
    },
  },
];

// 契约限额（docs/SPEC-ai-drawer-tools.md 第 4 条）：单次结果 2000 字、每轮 3 次、
// 往返 2 轮、工具总耗时 8s。前三个是纯逻辑，后一个由 ai-chat.js 的 AbortController 执行。
export const TOOL_MAX_CHARS = 2000;
export const MAX_CALLS_PER_ROUND = 3;
export const MAX_TOOL_ROUNDS = 2;
export const TOOL_TIMEOUT_MS = 8000;
// 网络层偶发卡死只重试一次；剩余预算不足 2s 就不值得再起一次（还会挤压后续轮次）。
export const TOOL_RETRY_MIN_MS = 2000;

const DEFAULT_ROOT = '/api/';
const SUMMARY_MAX_KEYS = 40;
const SUMMARY_KEYS_SHOWN = 16;
const SUMMARY_NESTED_KEYS = 6;
const SUMMARY_STRING_MAX = 80;
const SAMPLE_EDGE = 3;

/** 只按名字取回目录项（未知工具返回 null，由执行器给出结构化错误）。 */
export function toolByName(name) {
  return TOOLS.find((t) => t && t.function && t.function.name === name) || null;
}

/**
 * 点号路径下钻。数组用数字下标（data.series.0.value）。
 * 找不到时把失败层「可用的 key 列表」一起带回来——模型第二次才知道该改哪一段。
 * @returns {{found: boolean, value: unknown, available: string[]}}
 */
export function jsonPath(value, path) {
  const raw = String(path == null ? '' : path).trim();
  if (!raw) return { found: true, value, available: availableKeys(value) };
  const segs = raw.split('.').filter((s) => s !== '');
  let cur = value;
  for (let i = 0; i < segs.length; i += 1) {
    const key = segs[i];
    const keys = availableKeys(cur);
    if (cur === null || cur === undefined || typeof cur !== 'object') {
      return { found: false, value: undefined, available: keys };
    }
    if (Array.isArray(cur)) {
      const idx = Number(key);
      if (!Number.isInteger(idx) || idx < 0 || idx >= cur.length) {
        return { found: false, value: undefined, available: keys };
      }
      cur = cur[idx];
      continue;
    }
    if (!Object.prototype.hasOwnProperty.call(cur, key)) {
      return { found: false, value: undefined, available: keys };
    }
    cur = cur[key];
  }
  return { found: true, value: cur, available: availableKeys(cur) };
}

/** 某层可用的下钻键；数组不列 365 个下标，只报长度与下钻方式。 */
function availableKeys(v) {
  if (Array.isArray(v)) return ['array 长度 ' + v.length + '，用数字下标下钻'];
  if (v && typeof v === 'object') return Object.keys(v).slice(0, SUMMARY_MAX_KEYS);
  return [];
}

/** 截断字符串并标注（只在「下钻后仍是超长标量」这条兜底路径用到）。 */
export function truncateText(text, max = TOOL_MAX_CHARS) {
  const s = String(text == null ? '' : text);
  const limit = Math.max(16, Number(max) || TOOL_MAX_CHARS);
  if (s.length <= limit) return s;
  let marker = '\n<truncated 共 ' + s.length + ' 字，已截断>';
  if (marker.length >= limit) marker = '<truncated>';
  return s.slice(0, Math.max(0, limit - marker.length)) + marker;
}

/** 失败归形单源：{error, endpoint, status}（extra 用于补 available 这类诊断字段）。 */
export function toolError(endpoint, status, message, extra) {
  const out = {
    error: String(message == null ? '工具执行失败' : message),
    endpoint: String(endpoint || ''),
    status: Number(status) || 0,
  };
  if (extra && typeof extra === 'object') {
    Object.keys(extra).forEach((k) => {
      if (!Object.prototype.hasOwnProperty.call(out, k)) out[k] = extra[k];
    });
  }
  return out;
}

/** JSON.stringify 不抛（循环引用/undefined 只发生在本地调试数据，不该拖垮工具链）。 */
export function safeStringify(value) {
  try {
    const s = JSON.stringify(value);
    return typeof s === 'string' ? s : String(value);
  } catch (e) {
    return String(value);
  }
}

/**
 * 工具结果序列化（体积策略是这条工具链的核心）：
 *   1. ≤2000 字 → 原样返回；
 *   2. >2000 且没给 json_path → 顶层结构摘要（键名/类型/长度/短标量）+「用 json_path 下钻」；
 *   3. 给了 json_path 且下钻结果是长数组 → 首尾各 3 条采样（不硬截半截 JSON）；
 *   4. 下钻对象仍过大 → 该层结构摘要，示例路径带上已下钻的前缀。
 * 硬截 JSON 只会让模型看到断在数字中间的垃圾，比不给还糟。
 */
export function renderToolResult(value, opts = {}) {
  const maxChars = Math.max(200, Number(opts.maxChars) || TOOL_MAX_CHARS);
  const path = typeof opts.jsonPath === 'string' ? opts.jsonPath.trim() : '';
  const full = safeStringify(value);
  if (full.length <= maxChars) return full;
  if (Array.isArray(value)) return safeStringify(sampleArray(value, full.length, maxChars));
  if (value && typeof value === 'object') {
    return safeStringify(summarizeObject(value, full.length, path, maxChars));
  }
  if (typeof value === 'string') return renderLongString(value, maxChars);
  return truncateText(full, maxChars);
}

/** 超长标量：先截内容，再 JSON 转义；转义会膨胀，循环收到达标。 */
function renderLongString(value, maxChars) {
  let keep = Math.max(0, maxChars - 2);
  for (let i = 0; i < 24 && keep >= 0; i += 1) {
    const out = safeStringify(truncateText(value, keep));
    if (out.length <= maxChars) return out;
    keep -= out.length - maxChars + 8;
  }
  return safeStringify('<truncated>');
}

function sampleArray(arr, totalChars, maxChars) {
  const note =
    '数组过长（共 ' +
    arr.length +
    ' 条，' +
    totalChars +
    ' 字），已采样首尾各若干条；可用 json_path 继续下钻到更细的层。';
  const edges = [SAMPLE_EDGE, 1, 0];
  for (const edge of edges) {
    const out = { _note: note, total: arr.length };
    if (edge) {
      out.first = arr.slice(0, edge);
      out.last = arr.length <= edge * 2 ? arr.slice(edge) : arr.slice(-edge);
    }
    if (safeStringify(out).length <= maxChars) return out;
  }
  return { _note: note, total: arr.length };
}

function summarizeObject(obj, totalChars, prefix, maxChars) {
  const keys = Object.keys(obj);
  const example = suggestPath(obj, prefix);
  const note =
    '结果过大（' +
    totalChars +
    ' 字）已摘要：只列键名/类型/长度/短标量。请用 json_path 下钻' +
    (example ? '，如 ' + example : '') +
    '。';
  const build = (picked) => {
    const summary = {};
    picked.forEach((k) => {
      summary[k] = describeValue(obj[k]);
    });
    if (picked.length < keys.length) {
      summary._more = '另有 ' + (keys.length - picked.length) + ' 个键未列';
    }
    return { _note: note, total_chars: totalChars, summary };
  };
  let picked = keys.slice(0, SUMMARY_MAX_KEYS);
  let out = build(picked);
  while (safeStringify(out).length > maxChars && picked.length > 1) {
    picked = picked.slice(0, Math.max(1, Math.floor(picked.length / 2)));
    out = build(picked);
  }
  // 兜底：键名本身极长时连一个 key 都放不下，退回最短摘要（仍是合法 JSON）
  if (safeStringify(out).length > maxChars) {
    return { _note: '结果过大已摘要，请用 json_path 下钻。', total_chars: totalChars };
  }
  return out;
}

/** 结构摘要里的单个值：短标量原样给（模型要读数），容器给类型/长度/子键。 */
export function describeValue(v) {
  if (v === null) return null;
  const t = typeof v;
  if (t === 'string') {
    return v.length > SUMMARY_STRING_MAX ? v.slice(0, SUMMARY_STRING_MAX) + '…' : v;
  }
  if (t === 'number' || t === 'boolean') return v;
  if (Array.isArray(v)) {
    const first = v.length ? v[0] : null;
    if (first && typeof first === 'object' && !Array.isArray(first)) {
      // 对象数组必须带首元素的键：否则模型只知道 array[11]，下一轮只能瞎猜
      // json_path，白花一次工具预算（T1 端到端实测到过这个问题）。
      const keys = Object.keys(first);
      const shown = keys.slice(0, SUMMARY_KEYS_SHOWN).join(', ');
      const more = keys.length > SUMMARY_KEYS_SHOWN ? ', …' : '';
      return 'array[' + v.length + '] of object{' + shown + more + '}';
    }
    if (Array.isArray(first)) return 'array[' + v.length + '] of array';
    return 'array[' + v.length + ']';
  }
  if (t === 'object') {
    const keys = Object.keys(v);
    const shown = keys
      .slice(0, SUMMARY_KEYS_SHOWN)
      .map((k) => keyLabel(k, v[k]))
      .join(', ');
    return (
      'object[' + keys.length + ']{' + shown + (keys.length > SUMMARY_KEYS_SHOWN ? ', …' : '') + '}'
    );
  }
  return String(v);
}

/**
 * 对象摘要里的键名：数组值顺带标出长度与首元素子键。
 * 这样顶层摘要里出现 `yield_curve: object{..., tenors: array[11]{tenor,yield,change}}`
 * 时，模型第二次就能直接钻 `yield_curve.tenors`，不用先钻一层对象再钻数组。
 */
function keyLabel(key, v) {
  if (!Array.isArray(v)) return key;
  if (!v.length) return key + ':array[0]';
  const first = v[0];
  if (first && typeof first === 'object' && !Array.isArray(first)) {
    const keys = Object.keys(first);
    const shown = keys.slice(0, SUMMARY_NESTED_KEYS).join(',');
    const more = keys.length > SUMMARY_NESTED_KEYS ? ',…' : '';
    return key + ':array[' + v.length + ']{' + shown + more + '}';
  }
  return key + ':array[' + v.length + ']';
}

/** 摘要里的示例下钻路径：优先第一个「对象里的数组/对象」，退化到第一个容器键。 */
function suggestPath(obj, prefix) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return prefix;
  const keys = Object.keys(obj);
  for (const key of keys) {
    const v = obj[key];
    const p = prefix ? prefix + '.' + key : key;
    if (Array.isArray(v)) return p;
    if (v && typeof v === 'object') {
      const child = suggestPath(v, p);
      return child || p;
    }
  }
  return prefix;
}

/** 参数是模型吐出来的字符串：空串按 {} 处理，非对象/坏 JSON 明确拒绝。 */
export function parseToolArgs(raw) {
  const s = String(raw == null ? '' : raw).trim();
  if (!s) return { ok: true, value: {} };
  let v;
  try {
    v = JSON.parse(s);
  } catch (e) {
    return { ok: false, value: null, error: '工具参数不是合法 JSON：' + truncateText(s, 120) };
  }
  if (!v || typeof v !== 'object' || Array.isArray(v)) {
    return { ok: false, value: null, error: '工具参数必须是 JSON 对象' };
  }
  return { ok: true, value: v };
}

/**
 * 执行一次工具调用（永不抛）：
 *   参数校验 → 白名单校验 → GET /api/{endpoint} → 可选 json_path 下钻 → 体积策略序列化。
 * fetch 通过 opts.fetchImpl 注入（默认 globalThis.fetch），node 测试不需要起服务。
 * @returns {Promise<{callId: string, endpoint: string, content: string}>}
 */
export async function runToolCall(call, opts = {}) {
  const callId = String((call && call.id) || '');
  let endpoint = '';
  try {
    const fn = (call && call.function) || {};
    const name = String(fn.name || '');
    if (name !== TOOL_NAME) {
      return {
        callId,
        endpoint: '',
        content: safeStringify(toolError('', 0, '未知工具：' + (name || '(空)'))),
      };
    }
    const parsed = parseToolArgs(fn.arguments);
    if (!parsed.ok) {
      return { callId, endpoint: '', content: safeStringify(toolError('', 0, parsed.error)) };
    }
    const args = parsed.value || {};
    const wanted = typeof args.endpoint === 'string' ? args.endpoint.trim() : '';
    const path = typeof args.json_path === 'string' ? args.json_path.trim() : '';
    if (TOOL_ENDPOINTS.indexOf(wanted) < 0) {
      return {
        callId,
        endpoint: '',
        content: safeStringify(toolError(wanted, 0, '端点不在白名单：' + (wanted || '(空)'))),
      };
    }
    endpoint = wanted;
    if (args.json_path !== undefined && typeof args.json_path !== 'string') {
      return {
        callId,
        endpoint,
        content: safeStringify(toolError(endpoint, 0, 'json_path 必须是字符串')),
      };
    }
    const fetchImpl =
      typeof opts.fetchImpl === 'function'
        ? opts.fetchImpl
        : typeof globalThis.fetch === 'function'
          ? globalThis.fetch
          : null;
    if (!fetchImpl) {
      return {
        callId,
        endpoint,
        content: safeStringify(toolError(endpoint, 0, '当前环境没有 fetch，无法查询站内数据')),
      };
    }
    const root = typeof opts.root === 'string' && opts.root ? opts.root : DEFAULT_ROOT;
    const url = root.replace(/\/+$/, '/') + endpoint;
    let res = null;
    let attempt = 0;
    // 网络层偶发卡顿（浏览器传输层没把 GET 发出去）只重试一次；
    // 用户主动停止、预算不足、HTTP 4xx/5xx 都不重试。
    for (;;) {
      attempt += 1;
      try {
        res = await fetchImpl(url, {
          method: 'GET',
          headers: { accept: 'application/json' },
          signal: opts.signal,
        });
      } catch (e) {
        const userAborted = isUserAborted(opts);
        const msg = userAborted
          ? '工具执行已取消（用户停止）'
          : isAbort(e)
            ? '工具执行超时或已取消'
            : '网络请求失败：' + truncateText((e && e.message) || e, 160);
        if (retryAllowed(opts, attempt, 0)) continue;
        return {
          callId,
          endpoint,
          content: safeStringify(toolError(endpoint, 0, msg + retryNote(attempt))),
        };
      }
      const status = res && Number.isInteger(res.status) ? res.status : 0;
      if (!res || !res.ok) {
        const userAborted = isUserAborted(opts);
        const msg = userAborted
          ? '工具执行已取消（用户停止）'
          : status === 404
            ? '本期未导出（端点 404）'
            : status === 0
              ? '网络请求失败：端点没有返回状态'
              : '端点返回 ' + status;
        if (retryAllowed(opts, attempt, status)) continue;
        return {
          callId,
          endpoint,
          content: safeStringify(toolError(endpoint, status, msg + retryNote(attempt))),
        };
      }
      break;
    }
    const status = res && Number.isInteger(res.status) ? res.status : 0;
    if (typeof res.json !== 'function') {
      return {
        callId,
        endpoint,
        content: safeStringify(toolError(endpoint, status, '端点没有返回 JSON')),
      };
    }
    let data;
    try {
      data = await res.json();
    } catch (e) {
      return {
        callId,
        endpoint,
        content: safeStringify(toolError(endpoint, status, '端点返回的不是合法 JSON')),
      };
    }
    let picked = data;
    if (path) {
      const down = jsonPath(data, path);
      if (!down.found) {
        return {
          callId,
          endpoint,
          content: safeStringify(
            toolError(endpoint, status, 'JSON 路径不存在：' + path, { available: down.available })
          ),
        };
      }
      picked = down.value;
    }
    const maxChars = Number(opts.maxChars) || TOOL_MAX_CHARS;
    return {
      callId,
      endpoint,
      content: renderToolResult(picked, { maxChars, jsonPath: path }),
    };
  } catch (e) {
    return {
      callId,
      endpoint,
      content: safeStringify(
        toolError(endpoint, 0, '工具执行异常：' + truncateText((e && e.message) || e, 160))
      ),
    };
  }
}

function isAbort(e) {
  if (!e) return false;
  return e.name === 'AbortError' || e.code === 20 || /abort/i.test(String(e.message || ''));
}

/** 用户是否已经主动停止（外层 turn signal）。 */
function isUserAborted(opts) {
  return !!(opts && opts.outerSignal && opts.outerSignal.aborted);
}

/** 重试后仍失败时给结果加一句，排障/模型都能看出这不是首次失败。 */
function retryNote(attempt) {
  return attempt > 1 ? '；已重试一次' : '';
}

function retryAllowed(opts, attempt, status) {
  return canRetryTool({
    attempt,
    status,
    userAborted: isUserAborted(opts),
    signalAborted: !!(opts && opts.signal && opts.signal.aborted),
    remainingMs: retryBudgetLeft(opts),
  });
}

/**
 * 剩余工具预算（毫秒）：优先绝对 deadline，其次一次性 budgetMs，都没有按 0 处理
 * （直接调 runToolCall 的探针/单测不会意外触发重试）。
 */
export function retryBudgetLeft(opts = {}, now = Date.now()) {
  const deadline = Number(opts && opts.deadline);
  if (Number.isFinite(deadline)) return Math.max(0, deadline - now);
  const budget = Number(opts && opts.budgetMs);
  return Number.isFinite(budget) ? Math.max(0, budget) : 0;
}

/** 网络层失败是否值得重试：只一次、status 0、非用户 abort、剩余预算 ≥2s。 */
export function canRetryTool(state = {}) {
  const minMs = Number(state.minMs) || TOOL_RETRY_MIN_MS;
  return (
    state.attempt === 1 &&
    state.status === 0 &&
    !state.userAborted &&
    !state.signalAborted &&
    Number(state.remainingMs) >= minMs
  );
}

// ── 流式 SSE：分片解析与累积（纯函数；ai-chat.js 的 sseDelta / consume 都用这里） ──
//
// 工具调用在流里是「同一条调用拆成多个 chunk，按 index 归并」：
// 首片带 id / name，后续片只带 arguments 字符串的一段；finish_reason 在最后一个 chunk。

/** 解析一行 SSE：→ {content, reasoning, toolCalls, finishReason} / '[DONE]' / null。 */
export function parseSseLine(line) {
  const t = String(line == null ? '' : line).trim();
  if (t.slice(0, 5) !== 'data:') return null;
  const payload = t.slice(5).trim();
  if (payload === '[DONE]') return '[DONE]';
  let j;
  try {
    j = JSON.parse(payload);
  } catch (e) {
    return { content: '', reasoning: '', toolCalls: [], finishReason: '' };
  }
  const choice = j && j.choices && j.choices[0];
  const d = (choice && (choice.delta || choice.message)) || {};
  return normalizeDelta(d, choice && choice.finish_reason);
}

/**
 * 把一个 delta / message 归一成 consume 认识的四字段。
 * 非流式整包（choices[0].message）与 SSE 分片（choices[0].delta）共用这一份，
 * 避免「流式认 tool_calls、整包不认」这类分叉。
 */
export function normalizeDelta(d, finishReason) {
  const delta = d || {};
  const raw = Array.isArray(delta.tool_calls) ? delta.tool_calls : [];
  const toolCalls = raw.map((c, i) => {
    const fn = (c && c.function) || {};
    const args = fn.arguments;
    return {
      index: Number.isInteger(c && c.index) ? c.index : i,
      id: (c && c.id) || '',
      type: (c && c.type) || 'function',
      function: {
        name: typeof fn.name === 'string' ? fn.name : '',
        arguments: typeof args === 'string' ? args : args == null ? '' : safeStringify(args),
      },
    };
  });
  return {
    content: typeof delta.content === 'string' ? delta.content : '',
    reasoning: typeof delta.reasoning_content === 'string' ? delta.reasoning_content : '',
    toolCalls,
    finishReason: finishReason || delta.finish_reason || '',
  };
}

/** 把一批分片归并进已有调用列表（返回新数组，不改入参）。 */
export function accumulateToolCalls(calls, deltas) {
  const out = (Array.isArray(calls) ? calls : []).map(cloneCall);
  (Array.isArray(deltas) ? deltas : []).forEach((d) => {
    if (!d) return;
    const idx = Number.isInteger(d.index) ? d.index : out.length;
    let slot = out.find((c) => c.index === idx);
    if (!slot) {
      slot = { index: idx, id: '', type: 'function', function: { name: '', arguments: '' } };
      out.push(slot);
    }
    if (d.id && !slot.id) slot.id = String(d.id);
    if (d.type) slot.type = String(d.type);
    if (d.function) {
      if (d.function.name && !slot.function.name) slot.function.name = String(d.function.name);
      const part = d.function.arguments;
      if (part !== undefined && part !== null) {
        slot.function.arguments += typeof part === 'string' ? part : safeStringify(part);
      }
    }
  });
  return out;
}

/** 累积结果 → 可回填请求的 assistant.tool_calls（补 id、按 index 排序、去掉 index）。 */
export function finalizeToolCalls(calls) {
  return (Array.isArray(calls) ? calls : [])
    .slice()
    .sort((a, b) => (a.index || 0) - (b.index || 0))
    .map((c, i) => ({
      id: c.id || 'call_' + i,
      type: 'function',
      function: {
        name: (c.function && c.function.name) || '',
        arguments: (c.function && c.function.arguments) || '{}',
      },
    }));
}

function cloneCall(c) {
  return {
    index: Number.isInteger(c.index) ? c.index : 0,
    id: c.id || '',
    type: c.type || 'function',
    function: {
      name: (c.function && c.function.name) || '',
      arguments: (c.function && c.function.arguments) || '',
    },
  };
}

// ── 循环限额（纯函数；ai-chat.js 的 runTurn 只负责执行） ──────────────────

/**
 * 下一轮该用哪个 tool_choice。
 * 预算内且模型还要工具 → 'auto'；往返轮数用尽但仍有 tool_calls → 'none'，
 * 让 runTurn 再发一次请求强制模型基于已有信息开口（否则正文为空会落成空白）。
 * @param {number} roundsDone 已完成的工具往返轮数
 * @param {boolean} hasCalls 上一轮流结束后是否攒到了 tool_calls
 * @param {number} maxRounds 往返上限
 * @returns {'auto'|'none'}
 */
export function nextToolChoice(roundsDone, hasCalls, maxRounds = MAX_TOOL_ROUNDS) {
  const done = Number.isFinite(roundsDone) ? roundsDone : 0;
  const limit = Number(maxRounds) || MAX_TOOL_ROUNDS;
  return hasCalls && done >= limit ? 'none' : 'auto';
}

/**
 * 本轮还能不能继续调工具、最多执行几个。
 * @param {Array} calls 本轮攒到的工具调用（已 finalize）
 * @param {number} roundsDone 已经完成的往返轮数
 * @returns {{round: number, calls: Array, dropped: number} | null}
 */
export function planToolRound(calls, roundsDone, limits = {}) {
  const list = Array.isArray(calls) ? calls.filter(Boolean) : [];
  if (!list.length) return null;
  const maxCalls = Number(limits.maxCalls) || MAX_CALLS_PER_ROUND;
  const maxRounds = Number(limits.maxRounds) || MAX_TOOL_ROUNDS;
  const done = Number.isFinite(roundsDone) ? roundsDone : 0;
  if (done >= maxRounds) return null;
  return {
    round: done + 1,
    calls: list.slice(0, maxCalls),
    dropped: Math.max(0, list.length - maxCalls),
  };
}

/**
 * runTurn 每轮流结束后的下一步：执行工具 / 强制开口 / 收尾。
 * 纯函数，便于直接测「rounds=2 且仍有 calls → 下一次请求 tool_choice:'none'」。
 * @returns {{kind:'tools', plan: object} | {kind:'force', toolChoice:'none'} | {kind:'stop'}}
 */
export function nextLoopAction(calls, roundsDone, forced) {
  if (forced) return { kind: 'stop' };
  const plan = planToolRound(calls, roundsDone);
  if (plan) return { kind: 'tools', plan };
  const list = Array.isArray(calls) ? calls.filter(Boolean) : [];
  if (list.length && nextToolChoice(roundsDone, true) === 'none') {
    return { kind: 'force', toolChoice: 'none' };
  }
  return { kind: 'stop' };
}

// ── 上游兼容兜底（spec 第 6 条的残余风险防线） ────────────────────────────
//
// 实测（2026-10-22）历史 assistant 缺 reasoning_content + tools 仍是 200，
// 但生产 base 与实测网关不是同一条链。若官方变严，症状是「带 tools 的第二轮 400」；
// runTurn 用下面两个纯函数把历史降级成「system + 最后一条 user」重发一次（保留 tools）。

/** 这个错误是不是「带 tools 的请求缺 reasoning_content」那类 400。 */
export function isReasoningReject(status, detail) {
  if (Number(status) !== 400) return false;
  return /reasoning[_ ]?content/i.test(String(detail || ''));
}

/** 降级消息表：只留 system 与最后一条 user（工具往返本来就不在 thread 里）。 */
export function minimalMessages(messages) {
  const list = Array.isArray(messages) ? messages : [];
  const out = [];
  const sys = list.find((m) => m && m.role === 'system');
  if (sys) out.push(sys);
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i] && list[i].role === 'user') {
      out.push(list[i]);
      break;
    }
  }
  return out;
}
