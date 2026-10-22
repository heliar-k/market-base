// functions/_lib/common.js — Pages Functions 共用的小工具（HTTP / 限流 / 校验 / LLM 配置）。
//
// 目录名以 `_` 开头，Cloudflare Pages 不会把它当成路由；只有 functions/api/*.js
// 会暴露为 /api/*。与 src/server.py 的同名路由保持同一套契约（见 docs/ai-chat-backend.md）：
//   POST /api/chat   → 透传上游 SSE，Key 只留在服务端
//   GET  /api/models → 代理可用的模型白名单
//   POST /api/notes  → 写 NOTES KV（未绑定则 503，提示回本地落 data/）

export const DEFAULT_BASE = 'https://api.deepseek.com';
export const DEFAULT_MODEL = 'deepseek-chat';
export const NOTES_MAX = 1_000_000; // markdown 字节上限
export const NOTES_BODY_MAX = 4 * 1024 * 1024; // JSON body（含 thread/转义）
export const MISSING_KEY =
  '本站代理未配置 LLM_API_KEY：请在服务端环境变量里设置' +
  '（本地 .env / 线上 Cloudflare Pages 项目），或点右上「⚙ 设置」填自己的 Key 直连。';
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
const ROLES = new Set(['system', 'user', 'assistant']);

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
  return Number.isFinite(n) && n > 0 ? Math.min(8000, Math.max(64, Math.round(n))) : 1200;
}

export function allowEffort(env) {
  return ['1', 'true', 'yes'].includes(
    String((env && env.LLM_CHAT_ALLOW_EFFORT) || '').toLowerCase()
  );
}

// ── 请求校验 ────────────────────────────────────────────────────────────────
export function validateMessages(input) {
  if (!Array.isArray(input) || !input.length) throw new HttpError(422, 'messages 不能为空');
  if (input.length > MAX_MESSAGES) throw new HttpError(413, `messages 最多 ${MAX_MESSAGES} 条`);
  let total = 0;
  const out = input.map((m) => {
    const role = String((m && m.role) || '').trim();
    if (!ROLES.has(role)) throw new HttpError(422, `非法 role：${role || '(空)'}`);
    const content = String((m && m.content) || '');
    total += content.length;
    return { role, content };
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
  name = name
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, '-')
    .replace(/^[ .-]+|[ .-]+$/g, '');
  if (!name) name = fallback || 'note';
  if (!/\.md$/i.test(name)) name += '.md';
  if (name.length > 120) name = name.slice(0, 117) + '.md';
  return name;
}
