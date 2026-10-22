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

// 具名端点目录（单源）：白名单、tool enum、提示词索引全部从这里派生。
// 加端点只改这一处；只暴露真正稳定的读数，`liquidity/overview_*.json` 那组
// 区间图表数据（830KB–2MB）刻意不进目录。
//   - path  静态站 /api/{path}（无 .json 后缀，Astro directory 路由）
//   - group 族名，toolCatalogLegend() 按它分行
//   - label 给模型看的短中文含义（同族端点必须能区分开）
export const TOOL_CATALOG = [
  { path: 'rates/yield-curve', group: 'rates', label: '期限结构与曲线形态' },
  { path: 'rates/real-rates', group: 'rates', label: '实际利率与盈亏平衡' },
  { path: 'rates/fed-funds', group: 'rates', label: '联邦基金利率与政策走廊' },
  { path: 'rates/auctions', group: 'rates', label: '国债拍卖需求与日历' },
  { path: 'rates/analysis', group: 'rates', label: '利率研判（规则引擎）' },
  { path: 'credit/overview', group: 'credit', label: '信用利差与融资成本总览' },
  { path: 'credit/stress', group: 'credit', label: '信用压力五分量指数' },
  { path: 'credit/cds', group: 'credit', label: '主权与银行 CDS 代理' },
  { path: 'liquidity/snapshot', group: 'liquidity', label: '流动性总览快照' },
  { path: 'liquidity/reserves', group: 'liquidity', label: '准备金与银行中介意愿' },
  { path: 'liquidity/rrp-tga', group: 'liquidity', label: 'RRP 与 TGA 余额' },
  { path: 'liquidity/fed-balance-sheet', group: 'liquidity', label: '美联储资产负债表' },
  { path: 'liquidity/operations', group: 'liquidity', label: '公开市场操作与 SRF' },
  { path: 'liquidity/subsurface', group: 'liquidity', label: '次表层融资压力' },
  { path: 'liquidity/transmission-chain', group: 'liquidity', label: '流动性压力指数 LPI' },
  { path: 'liquidity/global-dollar', group: 'liquidity', label: '全球美元与央行互换' },
  { path: 'volatility/dashboard', group: 'volatility', label: '波动率全景仪表盘' },
];

// 白名单是执行器唯一的放行依据：端点外一律拒绝且不发请求。
export const TOOL_ENDPOINTS = TOOL_CATALOG.map((t) => t.path);

/**
 * 按 group 生成紧凑的「英文端点名=中文含义」索引，供系统提示词使用。
 * 为什么不在提示词里手抄清单：17 个端点靠 enum 名猜不准（credit/overview 与
 * credit/stress 差别在哪，模型看不出来），而 tool description 有 500 字上限，
 * 塞不下整张表。索引由目录生成，TOOL_CATALOG 加端点提示词自动跟上。
 * @returns {string} 每个 group 一行，如 `rates: yield-curve=期限结构；…`
 */
export function toolCatalogLegend(catalog = TOOL_CATALOG) {
  const order = [];
  const groups = new Map();
  (Array.isArray(catalog) ? catalog : []).forEach((t) => {
    if (!t || !t.path || !t.group) return;
    const group = String(t.group);
    if (!groups.has(group)) {
      groups.set(group, []);
      order.push(group);
    }
    const name = String(t.path).split('/').pop();
    const label = t.label ? String(t.label) : name;
    groups.get(group).push(name + '=' + label);
  });
  return order.map((g) => g + ': ' + groups.get(g).join('；')).join('\n');
}

// 工具目录（工具 description 只写「查什么 + 有哪几族」，端点索引放系统提示词）。
export const TOOLS = [
  {
    type: 'function',
    function: {
      name: TOOL_NAME,
      description:
        '查询本站已导出的数据端点（rates 利率 / credit 信用 / liquidity 流动性 / volatility 波动率）。' +
        '用于页面摘要里没有的读数（其他页面、更长历史、其他标的）。返回 JSON 文本，' +
        '结果过大时按提示用 json_path 下钻。',
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
// 往返 2 轮、工具总耗时 8s。前三个是纯逻辑，8s 由 ai-chat.js 的 AbortController 执行；
// 单次尝试另有 3.5s 上限（T3），由 runToolCall 每次尝试自建 controller 执行。
export const TOOL_MAX_CHARS = 2000;
export const MAX_CALLS_PER_ROUND = 3;
export const MAX_TOOL_ROUNDS = 2;
export const TOOL_TIMEOUT_MS = 8000;
// 单次取数上限（T3）：一次 GET 卡死不该吃掉整轮 8s 预算。每次尝试独立计时，
// 超时通常还剩 ~4.5s，仍能按 canRetryTool 的规则重试一次；批次 controller 仍是硬上限。
export const TOOL_CALL_TIMEOUT_MS = 3500;
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
 *   参数校验 → 白名单校验 → 同轮缓存 → GET /api/{endpoint} → 可选 json_path 下钻
 *   → 体积策略序列化。
 * fetch 通过 opts.fetchImpl 注入（默认 globalThis.fetch），node 测试不需要起服务。
 * 每次尝试独立计时（上限 min(剩余预算, TOOL_CALL_TIMEOUT_MS)），超时只废掉这一次尝试，
 * 不废整轮；重试规则仍是 canRetryTool（status 0、剩余 ≥2s、非用户 abort）。
 * opts.cache 由 runTurn 每轮新建：值是 executeToolCall() 的 promise（先占位再 await），
 * 所以同一批 Promise.all 里的重复调用也只会发一次 GET；命中时 callId 用本次调用自己的。
 * opts.callTimeoutMs 只给测试/探针缩短单次上限，生产不传。
 * @returns {Promise<{callId: string, endpoint: string, content: string, ok: boolean,
 *   cached?: boolean}>}
 */
export async function runToolCall(call, opts = {}) {
  const callId = String((call && call.id) || '');
  const cache = opts.cache;
  const cacheable = !!cache && typeof cache.get === 'function' && typeof cache.set === 'function';
  let endpoint = '';
  // 非缓存路径（参数/白名单错误）的统一出口：没有可复用结果，直接结构化失败
  const fail = (ep, content) => ({ callId, endpoint: ep, content, ok: false });
  try {
    const fn = (call && call.function) || {};
    const name = String(fn.name || '');
    if (name !== TOOL_NAME) {
      return fail('', safeStringify(toolError('', 0, '未知工具：' + (name || '(空)'))));
    }
    const parsed = parseToolArgs(fn.arguments);
    if (!parsed.ok) {
      return fail('', safeStringify(toolError('', 0, parsed.error)));
    }
    const args = parsed.value || {};
    const wanted = typeof args.endpoint === 'string' ? args.endpoint.trim() : '';
    const path = typeof args.json_path === 'string' ? args.json_path.trim() : '';
    if (TOOL_ENDPOINTS.indexOf(wanted) < 0) {
      // 顶层 endpoint 保持空串（旧形状）：结构化错误 content 里才带模型传的原值
      return fail('', safeStringify(toolError(wanted, 0, '端点不在白名单：' + (wanted || '(空)'))));
    }
    endpoint = wanted;
    if (args.json_path !== undefined && typeof args.json_path !== 'string') {
      return fail(endpoint, safeStringify(toolError(endpoint, 0, 'json_path 必须是字符串')));
    }
    // 同轮缓存键：端点 + 下钻路径。同一轮里同键的结果不会变，失败也值得复用
    // （省下的是一次调用预算，不是一次重试）；跨轮必须换新 Map（结果有时效性）。
    const cacheKey = endpoint + '|' + path;
    const run = () => executeToolCall(endpoint, path, opts);
    if (!cacheable) {
      const entry = await run();
      return { callId, endpoint: entry.endpoint, content: entry.content, ok: entry.ok };
    }
    let pending = cache.get(cacheKey);
    const hit = !!pending;
    if (!pending) {
      // 先占位再 await：同一批 Promise.all 里后到的重复调用拿到的是这个 promise，
      // 不会两条都 miss 再各发一次 GET（模型同轮问两遍同一端点时很常见）。
      pending = run();
      cache.set(cacheKey, pending);
    }
    const entry = await pending;
    const out = { callId, endpoint: entry.endpoint, content: entry.content, ok: entry.ok };
    if (hit) out.cached = true; // callId 始终是本次调用自己的，tool_call_id 才对得上
    return out;
  } catch (e) {
    return fail(
      endpoint,
      safeStringify(toolError(endpoint, 0, '工具执行异常：' + truncateText((e && e.message) || e, 160)))
    );
  }
}

/** 工具结果条目（缓存里存的就是它的 promise）：content 决定 ok。 */
function toolEntry(endpoint, content) {
  return { endpoint, content, ok: !contentHasError(content) };
}

/**
 * 真正取数并序列化（不含 callId / 缓存包装）。永不 reject：
 * 缓存里存的是它返回的 promise，一旦 reject，并发等待者会拿到一个 rejected promise，
 * 后续命中还会变成 unhandled rejection。所有失败都收敛成 content 里的结构化错误。
 * @returns {Promise<{endpoint: string, content: string, ok: boolean}>}
 */
async function executeToolCall(endpoint, path, opts) {
  try {
    const fetchImpl =
      typeof opts.fetchImpl === 'function'
        ? opts.fetchImpl
        : typeof globalThis.fetch === 'function'
          ? globalThis.fetch
          : null;
    if (!fetchImpl) {
      return toolEntry(
        endpoint,
        safeStringify(toolError(endpoint, 0, '当前环境没有 fetch，无法查询站内数据'))
      );
    }
    const root = typeof opts.root === 'string' && opts.root ? opts.root : DEFAULT_ROOT;
    const url = root.replace(/\/+$/, '/') + endpoint;
    let attempt = 0;
    let data;
    let finalStatus = 0;
    // 网络层偶发卡顿（浏览器传输层没把 GET 发出去）只重试一次；
    // 用户主动停止、预算不足、HTTP 4xx/5xx 都不重试。
    for (;;) {
      attempt += 1;
      const a = await runToolAttempt(fetchImpl, url, opts);
      const res = a.res;
      const status = res && Number.isInteger(res.status) ? res.status : 0;
      const userAborted = !!(a.why && a.why.user) || isUserAborted(opts);
      const batchAborted =
        !userAborted && (!!(a.why && a.why.batch) || !!(opts.signal && opts.signal.aborted));
      const timedOut = !userAborted && !batchAborted && !!(a.why && a.why.timedOut);
      if (res && res.ok && !a.jsonError && !userAborted && !batchAborted && !timedOut) {
        data = a.data;
        finalStatus = status;
        break;
      }
      let failStatus = status;
      let msg;
      if (userAborted) {
        failStatus = 0;
        msg = '工具执行已取消（用户停止）';
      } else if (batchAborted) {
        failStatus = 0;
        msg = '工具执行超时（本轮 8s 预算已用尽）';
      } else if (timedOut) {
        failStatus = 0;
        // 本次尝试自己的计时器先响：上限可能是 3.5s，也可能是更短的剩余预算；
        // 批次 8s controller 先响的情况走上面的 batchAborted，不会被这里吞掉。
        msg = '工具执行超时（单次 ' + timeoutLabel(a.why && a.why.limit) + '）';
      } else if (!res) {
        failStatus = 0;
        msg = isAbort(a.error)
          ? '工具执行超时或已取消'
          : '网络请求失败：' + truncateText((a.error && a.error.message) || a.error, 160);
      } else if (a.jsonError) {
        msg = '端点返回的不是合法 JSON';
      } else if (status === 404) {
        msg = '本期未导出（端点 404）';
      } else if (status === 0) {
        msg = '网络请求失败：端点没有返回状态';
      } else {
        msg = '端点返回 ' + status;
      }
      if (retryAllowed(opts, attempt, failStatus)) continue;
      return toolEntry(
        endpoint,
        safeStringify(toolError(endpoint, failStatus, msg + retryNote(attempt)))
      );
    }
    let picked = data;
    if (path) {
      const down = jsonPath(data, path);
      if (!down.found) {
        return toolEntry(
          endpoint,
          safeStringify(
            toolError(endpoint, finalStatus, 'JSON 路径不存在：' + path, {
              available: down.available,
            })
          )
        );
      }
      picked = down.value;
    }
    const maxChars = Number(opts.maxChars) || TOOL_MAX_CHARS;
    return toolEntry(endpoint, renderToolResult(picked, { maxChars, jsonPath: path }));
  } catch (e) {
    return toolEntry(
      endpoint,
      safeStringify(toolError(endpoint, 0, '工具执行异常：' + truncateText((e && e.message) || e, 160)))
    );
  }
}

// ── 单次取数超时（T3 主修） ───────────────────────────────────────────────
//
// 为什么每次尝试单独建 controller：一次 GET 卡在传输层（实测 status=0 / 8002ms）
// 不该把整轮 8s 预算一起吃光。单次上限 3.5s 先触发时，通常还剩 ~4.5s，
// 重试规则（剩余 ≥2s）能再试一次；两次都超时才落成结构化错误。
// 三个信号谁先触发都中断本次尝试：
//   - 自己计时器 → 单次超时；
//   - opts.signal（批次 8s controller）→ 本轮预算用尽；
//   - opts.outerSignal（用户点停止）→ 取消。
// 计时器只负责 abort，真正的分类靠 why 标记（abort 之后仍能分清是哪一种）。

/** 单次尝试的标称上限（生产固定 3.5s；测试/探针可用 callTimeoutMs 缩短）。 */
function callTimeoutCap(opts) {
  const override = Number(opts && opts.callTimeoutMs);
  return Number.isFinite(override) && override > 0 ? override : TOOL_CALL_TIMEOUT_MS;
}

/** 本次尝试的计时上限：min(剩余总预算, TOOL_CALL_TIMEOUT_MS)。 */
function attemptLimitMs(opts) {
  const cap = callTimeoutCap(opts);
  const remaining = hasBudget(opts) ? retryBudgetLeft(opts) : cap;
  return Math.max(0, Math.min(remaining, cap));
}

function hasBudget(opts) {
  return (
    Number.isFinite(Number(opts && opts.deadline)) || Number.isFinite(Number(opts && opts.budgetMs))
  );
}

/** 超时文案里的秒数：3500 → 3.5s，80 → 0.1s。 */
function timeoutLabel(ms) {
  return Math.max(0.1, Math.round(Number(ms) / 100) / 10) + 's';
}

function abortError() {
  const e = new Error('aborted');
  e.name = 'AbortError';
  return e;
}

/**
 * 让一次 fetch 与 abort 赛跑：注入的 fetchImpl 可能不认 signal（测试里的「永不 resolve」），
 * 光靠 fetch 自己 reject 会让 await 永远挂着，所以这里必须自己判 abort。
 */
function raceAbort(task, signal) {
  const p = Promise.resolve(task);
  if (!signal) return p;
  if (signal.aborted) {
    // 尝试还没开始就已经被取消：task 仍被创建了（fetchImpl 已被调用），
    // 它之后的 rejection 必须有人接住，否则 node 会报 unhandled rejection。
    p.catch(() => {});
    return Promise.reject(abortError());
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener('abort', onAbort, { once: true });
    p.then(
      (v) => {
        signal.removeEventListener('abort', onAbort);
        resolve(v);
      },
      (e) => {
        signal.removeEventListener('abort', onAbort);
        reject(e);
      }
    );
  });
}

/** 一次尝试：独立 controller + 超时计时器 + 联动批次/用户 signal；finally 清干净监听。 */
async function runToolAttempt(fetchImpl, url, opts) {
  const limit = attemptLimitMs(opts);
  const why = { user: false, batch: false, timedOut: false, limit };
  const ctl = new AbortController();
  const onUser = () => {
    why.user = true;
    ctl.abort();
  };
  const onBatch = () => {
    if (!why.user) why.batch = true;
    ctl.abort();
  };
  // 信号可能在进入本次尝试前就已 abort（用户已停止 / 批次已超时）：先置标记再挂监听，
  // 否则 abort 事件已经错过，fetch 会拿着一个永远不触发的 signal 干等。
  if (opts.outerSignal && opts.outerSignal.aborted) onUser();
  else if (opts.signal && opts.signal.aborted) onBatch();
  if (opts.outerSignal && opts.outerSignal.addEventListener) {
    opts.outerSignal.addEventListener('abort', onUser);
  }
  if (opts.signal && opts.signal.addEventListener) {
    opts.signal.addEventListener('abort', onBatch);
  }
  let timer = 0;
  if (limit > 0 && !ctl.signal.aborted) {
    timer = setTimeout(() => {
      if (!why.user && !why.batch) why.timedOut = true;
      ctl.abort();
    }, limit);
  }
  try {
    if (limit <= 0) {
      // 剩余预算已经归零（批次级）：不是单次上限的问题
      if (!why.user) why.batch = true;
      return { why };
    }
    const res = await raceAbort(
      fetchImpl(url, {
        method: 'GET',
        headers: { accept: 'application/json' },
        signal: ctl.signal,
      }),
      ctl.signal
    );
    if (!res || !res.ok || typeof res.json !== 'function') return { res, why };
    let data;
    try {
      // 读 body 也归本次尝试管：fetch 已返回但 body 卡住时，单次超时同样要能把它掐掉
      data = await raceAbort(Promise.resolve(res.json()), ctl.signal);
    } catch (e) {
      return { res, why, jsonError: e };
    }
    return { res, data, why };
  } catch (e) {
    return { error: e, why };
  } finally {
    clearTimeout(timer);
    if (opts.outerSignal && opts.outerSignal.removeEventListener) {
      opts.outerSignal.removeEventListener('abort', onUser);
    }
    if (opts.signal && opts.signal.removeEventListener) {
      opts.signal.removeEventListener('abort', onBatch);
    }
  }
}

/** content 是否结构化错误（解析出 error 字段即失败；正常数据不是 JSON 对象也算成功）。 */
function contentHasError(content) {
  try {
    const j = JSON.parse(String(content));
    if (!j || typeof j !== 'object' || Array.isArray(j)) return false;
    return Object.prototype.hasOwnProperty.call(j, 'error');
  } catch (e) {
    return false;
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

/**
 * 一批工具结果 → 本轮「查到的」与「没取到的」端点（字符串数组，去重、保持出现顺序）。
 * 只认 endpoint 非空的结果：预算不足 / 用户停止时执行器的兜底结果没有端点，不算查过。
 * 同一端点本轮既有成功又有失败（不同 json_path）时按成功算，避免 meta 里出现
 * 「查数 A · 未取到 A」这种自相矛盾的同一行。
 * 抽成纯函数是为了让 runTurn 只负责收集结果，UI 文案（查数 / 未取到）由这里定形状。
 * @returns {{tools: string[], toolsFailed: string[]}}
 */
export function summarizeToolRuns(results) {
  const tools = [];
  const failed = [];
  (Array.isArray(results) ? results : []).forEach((r) => {
    if (!r || !r.endpoint) return;
    const endpoint = String(r.endpoint);
    // 兼容旧形状（没有 ok 标志）：仍能从结构化错误 content 判出失败
    const ok = typeof r.ok === 'boolean' ? r.ok : !contentHasError(r.content);
    const list = ok ? tools : failed;
    if (list.indexOf(endpoint) < 0) list.push(endpoint);
  });
  return {
    tools,
    toolsFailed: failed.filter((endpoint) => tools.indexOf(endpoint) < 0),
  };
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
