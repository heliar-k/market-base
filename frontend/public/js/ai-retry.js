// ai-retry.js — 伴读抽屉的传输层重试策略（纯逻辑，不碰 DOM / fetch，node 可直接测）。
//
// 为什么单独一个文件：runTurn 里混着流式渲染、工具循环与 abort 语义，
// 「这次失败值不值得再发一次」却是纯判断。抽出来才能单测退避与时序，
// ai-chat.js 的 postChat 只负责执行重试与写状态文案。
//
// 三条边界（都是刻意的）：
//   - 用户点「停止」不再重试；abort 与超时靠 cause 区分，不看 error.name
//     （浏览器对 abort / 超时 / 断网都抛 AbortError 或 TypeError，名字不可靠）；
//   - 4xx 里只有 408 / 425 / 429 重试，其余是参数或配置问题，重发结果一样；
//   - 已经开始吐正文的流不自动重发（会把答案叠成两份），交回手动「重试」。

/** 一次提问最多发几次（首次 + 2 次自动重试）。 */
export const CHAT_MAX_ATTEMPTS = 3;
/** 首次退避，之后每轮翻倍。 */
export const CHAT_RETRY_BASE_MS = 800;
/** 退避上限（Retry-After 超过这个值就不自动等了，直接告诉用户多久后再试）。 */
export const CHAT_RETRY_MAX_DELAY_MS = 6000;
/** 连接 / 等响应头：超过就当成「访问不了」，可重试。给足思考模式的慢首包余量。 */
export const CHAT_HEADER_TIMEOUT_MS = 60000;
/** 流式响应里两个分片的间隔：挂住不动 120 秒就是断流。 */
export const CHAT_IDLE_TIMEOUT_MS = 120000;
/** 超时最多再来一次（60 秒级的等待连着重试三次 = 三分钟，用户会以为卡死）。 */
export const CHAT_TIMEOUT_MAX_ATTEMPTS = 2;

// 服务端明确告诉你「不是偶发」的：缺 Key 的 503 重发三次也还是 503。
const FATAL_DETAIL = /LLM_API_KEY|本站代理未配置|接口不可用/;

const clamp01 = (v) => Math.min(1, Math.max(0, Number(v) || 0));

/**
 * 这些状态码再发一次有意义：限流 / 超时 / 网关抖动。
 * 501 / 505 是「这个接口不存在 / 协议不支持」，重发无意义，故意不在表里。
 */
export function isRetryableStatus(status) {
  const s = Number(status);
  if (!Number.isFinite(s)) return false;
  return [408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529].indexOf(s) >= 0;
}

/**
 * fetch 抛出来的错误是否值得重试。浏览器口径：Chrome「Failed to fetch」、
 * Safari「Load failed」、Firefox「NetworkError …」，三者都是 TypeError；
 * 老网关也可能抛普通 Error，所以消息里带 network / timeout 的也算。
 */
export function isRetryableError(err) {
  if (!err) return false;
  const name = String(err.name || '');
  if (name === 'AbortError') return false;
  if (name === 'TypeError' || name === 'NetworkError' || name === 'TimeoutError') return true;
  return /failed to fetch|load failed|networkerror|network error|timeout/i.test(String(err.message || err));
}

/** Retry-After：整数秒或 HTTP 日期，认不出来当 0（不退避，按指数退避走）。 */
export function retryAfterMs(value, now = Date.now()) {
  if (value === null || value === undefined) return 0;
  const raw = String(value).trim();
  if (!raw) return 0;
  if (/^\d+$/.test(raw)) return Math.max(0, Number(raw) * 1000);
  const at = Date.parse(raw);
  if (Number.isNaN(at)) return 0;
  return Math.max(0, at - now);
}

/** 第 attempt 次失败后的等待时长：指数退避 + ±20% 抖动（防同一时刻的批量重试撞车）。 */
export function retryDelayMs(attempt, rand = Math.random) {
  const n = Math.max(1, Math.floor(Number(attempt) || 1));
  const base = Math.min(CHAT_RETRY_BASE_MS * Math.pow(2, n - 1), CHAT_RETRY_MAX_DELAY_MS);
  const r = typeof rand === 'function' ? clamp01(rand()) : 0.5;
  return Math.round(base * (0.8 + 0.4 * r));
}

/**
 * 一次失败 → 要不要再发、等多久。
 * state: {attempt, maxAttempts, status, detail, error, cause, retryAfterMs, rand}
 * cause: '' | 'user'（用户点停止）| 'timeout'（本次尝试自己的超时）
 * @returns {{retry: boolean, reason: string, attempt?: number, delayMs?: number, waitMs?: number}}
 */
export function planRetry(state = {}) {
  const attempt = Math.max(1, Math.floor(Number(state.attempt) || 1));
  const max = Math.max(1, Math.floor(Number(state.maxAttempts) || CHAT_MAX_ATTEMPTS));
  if (state.cause === 'user') return { retry: false, reason: 'stopped' };
  if (attempt >= max) return { retry: false, reason: 'exhausted' };
  if (state.cause === 'timeout' && attempt >= CHAT_TIMEOUT_MAX_ATTEMPTS) {
    return { retry: false, reason: 'exhausted' };
  }
  // 缺 Key 这类「重发也一样的」优先于状态码判断（503 本身在可重试表里）
  if (FATAL_DETAIL.test(String(state.detail || ''))) return { retry: false, reason: 'fatal' };
  if (state.cause !== 'timeout') {
    if (state.error) {
      if (!isRetryableError(state.error)) return { retry: false, reason: 'fatal' };
    } else if (!isRetryableStatus(state.status)) {
      return { retry: false, reason: 'fatal' };
    }
  }
  const waitMs = Math.max(0, Number(state.retryAfterMs) || 0);
  if (waitMs > CHAT_RETRY_MAX_DELAY_MS) {
    // 服务端让等 60 秒：自动等会把抽屉钉死，交回用户（错误文案里说明要等多久）
    return { retry: false, reason: 'retry-after', waitMs };
  }
  return {
    retry: true,
    reason: 'transient',
    attempt: attempt + 1,
    delayMs: waitMs || retryDelayMs(attempt, state.rand),
  };
}

/**
 * 最终失败的一句人话（进入对话流的那条「请求失败：…」）。
 * f: {status, detail, error, cause, phase, timeoutMs, attempts, waitMs}
 */
export function failureText(f = {}) {
  const attempts = Math.max(1, Math.floor(Number(f.attempts) || 1));
  const secs = Math.round((Number(f.timeoutMs) || CHAT_HEADER_TIMEOUT_MS) / 1000);
  let msg;
  if (f.cause === 'timeout' && f.phase === 'stream') {
    msg = '响应中断（' + secs + ' 秒没有新数据）';
  } else if (f.cause === 'timeout') {
    msg = '等待响应超时（' + secs + ' 秒）';
  } else if (f.error) {
    const raw = String(f.error.message || f.error);
    msg = '网络错误（' + (raw || '连接失败') + '）';
  } else {
    // 服务端自己的句子已经带句号时不要拼出「。；」
    msg = String(f.detail || '接口返回 ' + (f.status || 0)).replace(/[。．.；;，,]+$/, '');
  }
  const bits = [];
  if (attempts > 1) bits.push('已自动重试 ' + (attempts - 1) + ' 次');
  if (Number(f.waitMs) > 0) bits.push('服务端要求 ' + Math.ceil(Number(f.waitMs) / 1000) + ' 秒后再试');
  return bits.length ? msg + '；' + bits.join('，') : msg;
}

// ── 一次请求的「闸门」：用户停止 + 本次尝试超时 → 一个 signal ──────────────
// 为什么要记 cause：abort / 超时 / 断网在浏览器里都抛 AbortError 或 TypeError，
// 只看 error.name 分不清「人按了停止」和「网断了」——后者该重试，前者绝不能。
// phase：header = 还没收到响应头；stream = 已经在吐分片（超时阈值与文案不同）。
// outer 是本轮 turn 的 signal；闸门自己的 signal 才交给 fetch。
export function openGate(outer) {
  const parent = outer || new AbortController().signal;
  const ctl = new AbortController();
  let cause = '';
  let phase = '';
  let timeoutMs = 0;
  let streaming = false;
  let timer = 0;
  const trip = (why) => {
    if (cause) return;
    cause = why;
    try {
      ctl.abort();
    } catch (e) {
      /* 已关闭 */
    }
  };
  const arm = (ms, at) => {
    clearTimeout(timer);
    phase = at || phase;
    timeoutMs = ms;
    timer = setTimeout(() => trip('timeout'), ms);
  };
  const onOuter = () => trip('user');
  if (parent.aborted) trip('user');
  else parent.addEventListener('abort', onOuter);
  return {
    signal: ctl.signal,
    cause: () => cause,
    phase: () => phase,
    timeoutMs: () => timeoutMs,
    arm,
    // 收到响应头：流式要防「吐一半挂住」；整包响应不设空闲超时
    // （网关忽略了 stream:true 时可能算很久，拿分片间隔去卡它会把正常长回答误判成断流）
    headers(ct) {
      streaming = String(ct || '').indexOf('event-stream') >= 0;
      if (streaming) arm(CHAT_IDLE_TIMEOUT_MS, 'stream');
      else clearTimeout(timer);
    },
    // 每个分片重置空闲计时
    tick() {
      if (streaming) arm(CHAT_IDLE_TIMEOUT_MS, 'stream');
    },
    dispose() {
      clearTimeout(timer);
      parent.removeEventListener('abort', onOuter);
    },
  };
}

/** 可被「停止」打断的等待（退避期间点停止要立刻生效，不能干等 6 秒）。 */
export function waitFor(ms, signal) {
  return new Promise((resolve) => {
    if (!ms || !signal || signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done);
  });
}
