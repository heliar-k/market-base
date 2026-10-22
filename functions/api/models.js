// GET /api/models — 本站代理可用的模型白名单（抽屉「探测模型」在代理模式下走这里）。
import {
  MISSING_KEY,
  RATE_LIMITS,
  apiKey,
  clientIp,
  json,
  models,
  rateLimit,
} from '../_lib/common.js';

export async function onRequestGet({ request, env }) {
  if (!apiKey(env)) return json({ error: MISSING_KEY }, 503);
  if (!rateLimit('models:' + clientIp(request), ...RATE_LIMITS.models)) {
    return json({ error: '请求过于频繁，请稍后再试。' }, 429);
  }
  return json({
    data: models(env).map((id) => ({ id, display_name: id })),
    proxy: true,
  });
}
