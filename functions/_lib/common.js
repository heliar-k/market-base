// functions/_lib/common.js — Pages Functions 共用的小工具（HTTP / 限流 / 校验 / LLM 配置）。
//
// 目录名以 `_` 开头，Cloudflare Pages 不会把它当成路由；只有 functions/api/*.js
// 会暴露为 /api/*。与 src/server.py 的同名路由保持同一套契约（见 docs/ai-chat-backend.md）：
//   POST /api/chat   → 透传上游 SSE，Key 只留在服务端
//   GET  /api/models → 代理可用的模型白名单
//   POST /api/notes  → 写 NOTES KV（未绑定则 503，提示回本地落 data/）

export const DEFAULT_BASE = 'https://api.deepseek.com';
// deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用（官方更新日志），
// 现行枚举只剩 deepseek-flash（思考/非思考同体，靠 thinking 字段切换）与 deepseek-v4-pro。
export const DEFAULT_MODEL = 'deepseek-flash';
export const NOTES_MAX = 1_000_000; // markdown 字节上限
export const NOTES_BODY_MAX = 4 * 1024 * 1024; // JSON body（含 thread/转义）
export const MISSING_KEY =
  '本站代理未配置 LLM_API_KEY：请在服务端环境变量里设置' +
  '（本地 .env / 线上 Cloudflare Pages 项目），或点输入框上方的「⋯ → ⚙ 设置」填自己的 Key 直连。';
// 与 src/server.py 的 _LIMITS、docs/ai-chat-backend.md 的表同步
export const RATE_LIMITS = {
  chat: [10, 60_000],
  models: [30, 60_000],
  notes: [20, 3_600_000],
};

// 40k 中文字符 ≈120KB UTF-8，留出 JSON 包装余量
const MAX_BODY = 256 * 1024;
const MAX_MESSAGES = 30;
const MAX_CHARS = 40_000;
// 工具相关限额与 src/chat_proxy.py 的同名常量逐条对齐（改一处必须改两处）
const MAX_TOOLS = 8;
const MAX_TOOL_CALLS = 8;
const TOOL_DESC_MAX = 500;
const TOOL_CALL_ID_MAX = 128;
const TOOL_NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;
const TOOL_CHOICES = new Set(['auto', 'none']);
const ROLES = new Set(['system', 'user', 'assistant', 'tool']);

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...headers,
    },
  });
}

export async function readJson(request, max = MAX_BODY) {
  const declared = Number(request.headers.get('content-length') || 0);
  if (declared > max) throw new HttpError(413, '请求体过大');
  const text = await request.text();
  if (new TextEncoder().encode(text).length > max) throw new HttpError(413, '请求体过大');
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new HttpError(400, '请求体不是合法 JSON');
  }
}

// ── LLM 配置（Pages 项目环境变量；本地 FastAPI 读 .env 的同名项）──────────────
export function apiKey(env) {
  return String((env && env.LLM_API_KEY) || '').trim();
}

export function baseUrl(env) {
  const raw = String((env && env.LLM_BASE_URL) || '').trim() || DEFAULT_BASE;
  return raw.replace(/\/+$/, '');
}

export function models(env) {
  const raw = String((env && env.LLM_CHAT_MODELS) || (env && env.LLM_MODEL) || '').trim();
  const list = raw
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean);
  return list.length ? list : [DEFAULT_MODEL];
}

export function resolveModel(env, requested) {
  const allowed = models(env);
  return allowed.includes(requested) ? requested : allowed[0];
}

export function maxTokens(env) {
  const n = Number(env && env.LLM_CHAT_MAX_TOKENS);
  if (Number.isFinite(n) && n > 0) return Math.min(8000, Math.max(64, Math.round(n)));
  // 思考模式下 reasoning token 计入 max_tokens：1200 会被思维链吃光、正文被截断
  return thinkingMode(env) === 'enabled' ? 4000 : 1200;
}

export function allowEffort(env) {
  return ['1', 'true', 'yes'].includes(String((env && env.LLM_CHAT_ALLOW_EFFORT) || '').toLowerCase());
}

/** 只有 DeepSeek 官方地址认 `thinking` 字段；别的 OpenAI 兼容网关会 400。 */
function isDeepSeek(base) {
  return String(base || '').includes('api.deepseek.com');
}

/**
 * 思考模式档位：'enabled' | 'disabled' | null（null = 不下发该字段）。
 * DeepSeek 侧默认开启并显式写进请求体——上游默认值会变，客户端不该依赖它。
 * `LLM_THINKING=off` 可关掉（`src/llm.py` 的叙事改写共用同一个开关）。
 */
export function thinkingMode(env) {
  if (!isDeepSeek(baseUrl(env))) return null;
  return ['0', 'false', 'no', 'off'].includes(String((env && env.LLM_THINKING) || '').toLowerCase())
    ? 'disabled'
    : 'enabled';
}

// ── 请求校验 ────────────────────────────────────────────────────────────────

/** 是「对象」而非 null / 数组（JSON Schema 的 parameters 只要求到这里）。 */
function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * 校验 tools（可选）：≤8 个、type 只准 function、name 形状、description ≤500、
 * parameters 是对象。**不校验 JSON Schema 内容**，通过后原样返回（不裁字段）。
 * 空数组视同未传（避免上游对 `tools: []` 的行为不确定）。
 */
export function validateTools(input) {
  if (input === undefined || input === null) return null;
  if (!Array.isArray(input)) throw new HttpError(422, 'tools 必须是数组');
  if (input.length > MAX_TOOLS) throw new HttpError(422, `tools 最多 ${MAX_TOOLS} 个`);
  if (!input.length) return null;
  input.forEach((tool, i) => {
    const at = `tools[${i}]`;
    if (!isPlainObject(tool)) throw new HttpError(422, `${at} 必须是对象`);
    if (tool.type !== 'function') throw new HttpError(422, `${at}.type 只支持 "function"`);
    const fn = tool.function;
    if (!isPlainObject(fn)) throw new HttpError(422, `${at}.function 必须是对象`);
    if (typeof fn.name !== 'string' || !TOOL_NAME_RE.test(fn.name)) {
      throw new HttpError(422, `${at}.function.name 必须是 1-64 位字母/数字/_/-`);
    }
    if (typeof fn.description !== 'string') {
      throw new HttpError(422, `${at}.function.description 必须是字符串`);
    }
    if (fn.description.length > TOOL_DESC_MAX) {
      throw new HttpError(422, `${at}.function.description 最多 ${TOOL_DESC_MAX} 字`);
    }
    if (!isPlainObject(fn.parameters)) {
      throw new HttpError(422, `${at}.function.parameters 必须是对象`);
    }
  });
  return input;
}

/**
 * 校验 tool_choice（可选）：只认 "auto" / "none"。
 * `required` 与具名函数在思考模式下会被上游 400
 * （雷火网关：Thinking mode does not support this tool_choice），所以入口就拒掉。
 */
export function validateToolChoice(input) {
  if (input === undefined || input === null) return null;
  if (!TOOL_CHOICES.has(input)) {
    throw new HttpError(422, 'tool_choice 只支持 "auto" / "none"（思考模式下上游不支持 required / 具名函数）');
  }
  return input;
}

/**
 * 校验消息列表并归一化（role/content/tool_call_id/tool_calls）。
 *
 * - `tool` 角色必须带 `tool_call_id`（≤128 字）。
 * - `assistant` 可带 `tool_calls`（≤8 条），**原样透传**（不重排、不裁字段）。
 * - 40k 上限仍按 content 累加，`tool_calls[].function.arguments` 也计入（否则可绕过）。
 * - `content` 允许 null / 缺失（assistant 只带工具调用时上游就发 null），归一成 ""。
 */
export function validateMessages(input) {
  if (!Array.isArray(input) || !input.length) throw new HttpError(422, 'messages 不能为空');
  if (input.length > MAX_MESSAGES) throw new HttpError(413, `messages 最多 ${MAX_MESSAGES} 条`);
  let total = 0;
  const out = input.map((m) => {
    if (!isPlainObject(m)) throw new HttpError(422, '非法消息：每条必须是对象');
    const role = String(m.role || '').trim();
    if (!ROLES.has(role)) throw new HttpError(422, `非法 role：${role || '(空)'}`);
    if (m.content !== undefined && m.content !== null && typeof m.content !== 'string') {
      throw new HttpError(422, 'content 必须是字符串或 null');
    }
    const content = m.content || '';
    total += content.length;
    const msg = { role, content };
    if (role === 'tool') {
      const id = m.tool_call_id;
      if (typeof id !== 'string' || !id.trim()) {
        throw new HttpError(422, 'tool 角色消息必须带 tool_call_id');
      }
      if (id.length > TOOL_CALL_ID_MAX) {
        throw new HttpError(422, `tool_call_id 最多 ${TOOL_CALL_ID_MAX} 字`);
      }
      msg.tool_call_id = id;
    }
    if (role === 'assistant' && m.tool_calls !== undefined && m.tool_calls !== null) {
      const calls = m.tool_calls;
      if (!Array.isArray(calls)) throw new HttpError(422, 'assistant.tool_calls 必须是数组');
      if (calls.length > MAX_TOOL_CALLS) {
        throw new HttpError(422, `assistant.tool_calls 最多 ${MAX_TOOL_CALLS} 条`);
      }
      for (const call of calls) {
        if (!isPlainObject(call)) throw new HttpError(422, 'tool_calls 每一项必须是对象');
        const fn = call.function;
        const args = isPlainObject(fn) ? fn.arguments : undefined;
        if (args === undefined || args === null) continue;
        if (typeof args !== 'string') {
          throw new HttpError(422, 'tool_calls[].function.arguments 必须是字符串');
        }
        total += args.length;
      }
      msg.tool_calls = calls;
    }
    return msg;
  });
  if (total > MAX_CHARS) throw new HttpError(413, `消息总长度超过 ${MAX_CHARS} 字`);
  if (!out.some((m) => m.role === 'user')) {
    throw new HttpError(422, 'messages 里至少要有一条 user 消息');
  }
  return out;
}

// ── 限流（每个 isolate 一份，best-effort；真正抗刷要靠 Cloudflare 层规则）──────
const buckets = new Map();

export function rateLimit(key, limit, windowMs) {
  const now = Date.now();
  const hits = (buckets.get(key) || []).filter((t) => now - t < windowMs);
  if (hits.length >= limit) {
    buckets.set(key, hits);
    return false;
  }
  hits.push(now);
  buckets.set(key, hits);
  if (buckets.size > 5000) {
    for (const k of [...buckets.keys()].slice(0, 2500)) buckets.delete(k);
  }
  return true;
}

export function clientIp(request) {
  const cf = request.headers.get('cf-connecting-ip');
  if (cf) return cf.trim();
  const fwd = request.headers.get('x-forwarded-for') || '';
  return fwd.split(',')[0].trim() || 'unknown';
}

// ── 笔记文件名 ──────────────────────────────────────────────────────────────
export function safeNoteName(raw, fallback = 'note') {
  let name = String(raw || '')
    .split(/[\\/]/)
    .pop()
    .trim();
  name = name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '-').replace(/^[ .-]+|[ .-]+$/g, '');
  if (!name) name = fallback || 'note';
  if (!/\.md$/i.test(name)) name += '.md';
  if (name.length > 120) name = name.slice(0, 117) + '.md';
  return name;
}
