// ai-config.js — 伴读抽屉的配置纯逻辑（不碰 DOM，可直接用 node 测）。
//
// 抽出来的原因：探测模型 / 换模型会重渲染设置面板，如果重渲染时从「已保存配置」
// 重建输入框，用户刚敲的 Base URL / Key 会被清空；推理强度也需要在重载后与
// 实际发送值保持一致。这两类判断放这里，ai-chat.js 只负责调用与渲染。

export const DEFAULT_CFG = {
  baseUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-4o-mini',
  effort: '',
};

export const normalizeBase = (v) => String(v || '').replace(/\/+$/, '');

/** 没填 Key 且 Base URL 保持默认 → 走本站 /api/chat 代理。 */
export function isProxyMode(cfg, defaultBase = DEFAULT_CFG.baseUrl) {
  return !(cfg && cfg.apiKey) && normalizeBase(cfg && cfg.baseUrl) === normalizeBase(defaultBase);
}

/** 模型表缓存只对「同一个 base + 同一个通道（代理/直连）」有效，防止代理白名单串到 BYOK。 */
export function modelCacheMatches(cache, base, proxy) {
  return (
    !!cache &&
    Array.isArray(cache.list) &&
    normalizeBase(cache.base) === normalizeBase(base) &&
    !!cache.proxy === !!proxy
  );
}

/** 请求体单源：代理模式不传 reasoning_effort；直连只发当前模型明确支持的档位。 */
export function chatPayload(cfg, messages, models, proxy) {
  const body = { model: cfg.model, stream: true, messages };
  const levels = effortLevels(models, cfg.model);
  if (!proxy && cfg.effort && levels.indexOf(cfg.effort) >= 0) {
    body.reasoning_effort = cfg.effort;
  }
  return body;
}

/** 网关模型表里该模型支持的推理强度档位（兼容两种字段名）。 */
export function effortLevels(models, modelId) {
  const m = (models || []).find((x) => x && x.id === modelId);
  if (!m) return [];
  if (Array.isArray(m.reasoning_levels)) {
    return m.reasoning_levels.filter(Boolean).map(String);
  }
  return (m.supported_reasoning_levels || [])
    .map((x) => x && x.effort)
    .filter(Boolean)
    .map(String);
}

/** 已保存的 effort 若不在当前模型档位里就清掉——否则 UI 显示「默认」但请求仍带着它。 */
export function reconcileCfg(cfg, models) {
  const out = Object.assign({}, cfg);
  if (out.effort && effortLevels(models, out.model).indexOf(out.effort) < 0) {
    out.effort = '';
  }
  return out;
}

/**
 * 从设置面板读回一份草稿（含尚未保存的输入）。
 * 面板没渲染（root 为空 / 缺字段）时回落到 fallback，避免把已保存值读丢。
 */
export function readCfgDraft(root, fallback) {
  const base = fallback || {};
  const value = (k) => {
    const el = root && root.querySelector ? root.querySelector('[data-k="' + k + '"]') : null;
    if (!el) return undefined;
    return String(el.value == null ? '' : el.value);
  };
  const pick = (k) => {
    const v = value(k);
    return v === undefined ? base[k] || '' : v;
  };
  return {
    baseUrl: pick('baseUrl') || base.baseUrl || '',
    apiKey: pick('apiKey'),
    model: pick('model') || base.model || '',
    effort: pick('effort'),
  };
}
