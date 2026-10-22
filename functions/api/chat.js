// POST /api/chat — 伴读抽屉的本站代理（Cloudflare Pages Function）。
//
// 浏览器只带对话内容，不带 Key；LLM_API_KEY 从 Pages 项目环境变量读取。
// 上游 SSE 原样透传（Response body 是 ReadableStream，不缓冲整段回答）。
import {
  MISSING_KEY,
  RATE_LIMITS,
  allowEffort,
  apiKey,
  baseUrl,
  clientIp,
  HttpError,
  json,
  maxTokens,
  rateLimit,
  readJson,
  resolveModel,
  thinkingMode,
  validateMessages,
} from '../_lib/common.js';

export async function onRequestPost({ request, env }) {
  try {
    if (!apiKey(env)) return json({ error: MISSING_KEY }, 503);
    if (!rateLimit('chat:' + clientIp(request), ...RATE_LIMITS.chat)) {
      return json(
        { error: `请求过于频繁（本站代理每分钟 ${RATE_LIMITS.chat[0]} 次），请稍后再试。` },
        429,
        { 'retry-after': '60' }
      );
    }

    const body = await readJson(request);
    const messages = validateMessages(body && body.messages);
    const payload = {
      model: resolveModel(env, body && body.model),
      messages,
      stream: true,
      max_tokens: maxTokens(env),
    };
    const effort = String((body && body.reasoning_effort) || '').trim();
    if (allowEffort(env) && ['minimal', 'low', 'medium', 'high'].includes(effort)) {
      payload.reasoning_effort = effort;
    }
    // 思考模式：DeepSeek 侧显式下发（默认 enabled），上游默认值不可依赖
    const think = thinkingMode(env);
    if (think) payload.thinking = { type: think };

    const upstream = await fetch(baseUrl(env) + '/chat/completions', {
      method: 'POST',
      headers: {
        authorization: 'Bearer ' + apiKey(env),
        'content-type': 'application/json',
        accept: 'text/event-stream',
      },
      body: JSON.stringify(payload),
    });

    if (!upstream.ok) {
      const detail = (await upstream.text()).slice(0, 2000);
      return json({ error: `上游 ${upstream.status}：${detail}` }, upstream.status);
    }

    const headers = new Headers();
    headers.set(
      'content-type',
      upstream.headers.get('content-type') || 'text/event-stream; charset=utf-8'
    );
    headers.set('cache-control', 'no-cache');
    headers.set('x-accel-buffering', 'no');
    headers.set('x-llm-model', String(payload.model));
    return new Response(upstream.body, { status: 200, headers });
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    return json({ error: '代理失败：' + (e && e.message ? e.message : e) }, 502);
  }
}
