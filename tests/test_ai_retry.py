"""伴读抽屉的传输层重试策略（frontend/public/js/ai-retry.js）—— node 直接跑。

覆盖五个回归点：
- 哪些失败值得再发一次（限流 / 网关 5xx / 浏览器网络错误），哪些绝不（4xx、缺 Key）；
- 退避是「指数 + 抖动」，Retry-After 优先；服务端让等太久就不自动等，改成人话提示；
- 超时最多再来一次——60 秒级等待连着三次 = 三分钟，用户会以为卡死；
- 闸门分得清「用户停止 / 超时 / 非流式响应」，断流计时不被整包响应误触；
- 等待可被「停止」立刻打断（退避期间不能干等）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "frontend" / "public" / "js"
RETRY = JS / "ai-retry.js"
CHAT = JS / "ai-chat.js"

BODY = """
const m = await import(%s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const err = (name, msg) => Object.assign(new Error(msg || ''), { name });
const T = (msg) => new TypeError(msg);
const statuses = [
  400, 401, 403, 404, 408, 413, 422, 425, 429, 500,
  501, 502, 503, 504, 505, 520, 522, 524, 529,
];

// ── 闸门：超时 / 用户停止 / dispose / 整包响应 ──────────────────────────────
const o1 = new AbortController();
const g1 = m.openGate(o1.signal);
g1.arm(20, 'header');
await sleep(70);
const gateTimeout = {
  aborted: g1.signal.aborted, cause: g1.cause(), phase: g1.phase(), ms: g1.timeoutMs(),
};
g1.dispose();

const o2 = new AbortController();
const g2 = m.openGate(o2.signal);
g2.arm(5000, 'header');
o2.abort();
await sleep(5);
const gateUser = { aborted: g2.signal.aborted, cause: g2.cause() };
g2.dispose();

const g3 = m.openGate(new AbortController().signal);
g3.arm(20, 'header');
g3.dispose();
await sleep(60);
const gateDisposed = { aborted: g3.signal.aborted, cause: g3.cause() };

const g4 = m.openGate(new AbortController().signal);
g4.arm(20, 'header');
g4.headers('application/json; charset=utf-8');
await sleep(60);
const gateBuffered = { aborted: g4.signal.aborted, cause: g4.cause() };
g4.dispose();

const g5 = m.openGate(new AbortController().signal);
g5.headers('text/event-stream');
let ticks = 0;
const timer = setInterval(() => { g5.tick(); ticks += 1; }, 20);
await sleep(80);
const gateStream = { aborted: g5.signal.aborted, cause: g5.cause(), ticks };
clearInterval(timer);
g5.dispose();

// ── 等待可被停止打断 ────────────────────────────────────────────────────────
const o6 = new AbortController();
const t6 = Date.now();
const p6 = m.waitFor(500, o6.signal);
setTimeout(() => o6.abort(), 20);
await p6;
const waitedStop = Date.now() - t6;
const t7 = Date.now();
await m.waitFor(0, new AbortController().signal);
const waitedZero = Date.now() - t7;

console.log(JSON.stringify({
  consts: {
    max: m.CHAT_MAX_ATTEMPTS,
    timeoutMax: m.CHAT_TIMEOUT_MAX_ATTEMPTS,
    header: m.CHAT_HEADER_TIMEOUT_MS,
    idle: m.CHAT_IDLE_TIMEOUT_MS,
    base: m.CHAT_RETRY_BASE_MS,
    maxDelay: m.CHAT_RETRY_MAX_DELAY_MS,
  },
  statuses: statuses.map((s) => [s, m.isRetryableStatus(s)]),
  errors: {
    chrome: m.isRetryableError(T('Failed to fetch')),
    safari: m.isRetryableError(err('TypeError', 'Load failed')),
    firefox: m.isRetryableError(T('NetworkError when attempting to fetch resource.')),
    timeout: m.isRetryableError(err('TimeoutError', 'The operation timed out')),
    abort: m.isRetryableError(err('AbortError', 'The user aborted a request')),
    plain: m.isRetryableError(new Error('boom')),
    none: m.isRetryableError(null),
  },
  retryAfter: {
    secs: m.retryAfterMs('60'),
    zero: m.retryAfterMs('0'),
    date: m.retryAfterMs(new Date(100000).toUTCString(), 0),
    junk: m.retryAfterMs('soon'),
    empty: m.retryAfterMs(''),
    none: m.retryAfterMs(null),
  },
  delays: {
    a1lo: m.retryDelayMs(1, () => 0),
    a1hi: m.retryDelayMs(1, () => 1),
    a2mid: m.retryDelayMs(2, () => 0.5),
    cap: m.retryDelayMs(9, () => 0.5),
  },
  plans: {
    network: m.planRetry({ attempt: 1, error: T('Failed to fetch'), rand: () => 0.5 }),
    lastAttempt: m.planRetry({ attempt: 3, error: T('Failed to fetch') }),
    stopped: m.planRetry({ attempt: 1, cause: 'user' }),
    badRequest: m.planRetry({ attempt: 1, status: 400, detail: 'bad args' }),
    rateLimited: m.planRetry({ attempt: 1, status: 429, retryAfterMs: 5000 }),
    tooLong: m.planRetry({ attempt: 1, status: 429, retryAfterMs: 60000 }),
    missingKey: m.planRetry({
      attempt: 1,
      status: 503,
      detail: '本站代理未配置 LLM_API_KEY：请在服务端环境变量里设置',
    }),
    timeoutFirst: m.planRetry({ attempt: 1, cause: 'timeout' }),
    timeoutSecond: m.planRetry({ attempt: 2, cause: 'timeout' }),
    abortErr: m.planRetry({ attempt: 1, error: err('AbortError', 'x') }),
  },
  texts: {
    network: m.failureText({ error: T('Failed to fetch'), attempts: 3 }),
    header: m.failureText({ cause: 'timeout', phase: 'header', timeoutMs: 60000, attempts: 1 }),
    stream: m.failureText({ cause: 'timeout', phase: 'stream', timeoutMs: 120000, attempts: 1 }),
    http: m.failureText({ status: 429, detail: '请求过于频繁。', attempts: 1, waitMs: 60000 }),
    plain: m.failureText({ status: 500, detail: '上游 500', attempts: 1 }),
  },
  gate: {
    timeout: gateTimeout,
    user: gateUser,
    disposed: gateDisposed,
    buffered: gateBuffered,
    stream: gateStream,
  },
  waits: { stop: waitedStop, zero: waitedZero },
}));
"""


def _run() -> dict:
    return run_node(BODY % json.dumps(RETRY.as_uri()), timeout=30)


def test_only_transient_statuses_are_retryable() -> None:
    out = dict(_run()["statuses"])
    for s in (408, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529):
        assert out[s] is True, f"{s} 应该重试"
    # 参数/配置问题重发结果一样；501/505 是「没有这个接口」，也不该重试
    for s in (400, 401, 403, 404, 413, 422, 501, 505):
        assert out[s] is False, f"{s} 不该重试"


def test_network_errors_are_retryable_but_abort_is_not() -> None:
    out = _run()["errors"]
    assert out["chrome"] is True
    assert out["safari"] is True
    assert out["firefox"] is True
    assert out["timeout"] is True
    assert out["abort"] is False, "用户按停止抛的 AbortError 绝不能重试"
    assert out["plain"] is False
    assert out["none"] is False


def test_retry_after_parses_seconds_and_http_date() -> None:
    out = _run()["retryAfter"]
    assert out["secs"] == 60000
    assert out["zero"] == 0
    assert out["date"] == 100000
    assert out["junk"] == 0
    assert out["empty"] == 0
    assert out["none"] == 0


def test_backoff_is_exponential_with_bounded_jitter() -> None:
    out = _run()["delays"]
    assert out["a1lo"] == 640, "800ms × 0.8"
    assert out["a1hi"] == 960, "800ms × 1.2"
    assert out["a2mid"] == 1600
    assert out["cap"] == 6000, "退避封顶，不能无限翻倍"


def test_plan_retry_boundaries() -> None:
    out = _run()["plans"]
    assert out["network"]["retry"] is True
    assert out["network"]["attempt"] == 2
    assert 640 <= out["network"]["delayMs"] <= 960
    assert out["lastAttempt"] == {"retry": False, "reason": "exhausted"}
    assert out["stopped"] == {"retry": False, "reason": "stopped"}
    assert out["badRequest"] == {"retry": False, "reason": "fatal"}
    assert out["rateLimited"]["retry"] is True
    assert out["rateLimited"]["delayMs"] == 5000, "服务端给的 Retry-After 优先于指数退避"
    assert out["tooLong"]["retry"] is False
    assert out["tooLong"]["reason"] == "retry-after"
    assert out["tooLong"]["waitMs"] == 60000
    assert out["missingKey"] == {"retry": False, "reason": "fatal"}, "缺 Key 的 503 重试无意义"
    assert out["timeoutFirst"]["retry"] is True
    assert out["timeoutSecond"] == {"retry": False, "reason": "exhausted"}, "超时只再给一次机会"
    assert out["abortErr"] == {"retry": False, "reason": "fatal"}


def test_failure_text_is_one_readable_sentence() -> None:
    out = _run()["texts"]
    assert "网络错误（Failed to fetch）" in out["network"]
    assert "已自动重试 2 次" in out["network"]
    assert out["header"] == "等待响应超时（60 秒）"
    assert out["stream"] == "响应中断（120 秒没有新数据）"
    # 服务端句子已带句号时不拼出「。；」，且要说明多久后再来
    assert out["http"] == "请求过于频繁；服务端要求 60 秒后再试"
    assert out["plain"] == "上游 500"
    assert "已自动重试" not in out["plain"]


def test_gate_separates_timeout_from_user_stop() -> None:
    gate = _run()["gate"]
    assert gate["timeout"] == {"aborted": True, "cause": "timeout", "phase": "header", "ms": 20}
    assert gate["user"] == {"aborted": True, "cause": "user"}
    assert gate["disposed"] == {"aborted": False, "cause": ""}, "dispose 之后计时器不能再开火"
    # 整包响应（网关忽略 stream:true）不设空闲超时：慢模型算很久也不能被当成断流
    assert gate["buffered"] == {"aborted": False, "cause": ""}
    assert gate["stream"]["aborted"] is False
    assert gate["stream"]["ticks"] >= 2, "分片到达要重置断流计时"


def test_wait_for_stop_is_immediate() -> None:
    waits = _run()["waits"]
    assert waits["stop"] < 200, "退避期间点停止要立刻收手，不能等满"
    assert waits["zero"] < 100


def test_chat_wires_retry_and_keeps_failures_out_of_context() -> None:
    """接线守卫：runTurn 不能再绕过 postChat 直发，失败条目也要有标记。"""
    src = CHAT.read_text(encoding="utf-8")
    assert "await postChat(cfg, reqMessages, turn, choice, onRetryStatus)" in src
    assert "openGate(turn.signal)" in src
    assert "request(cfg, reqMessages, turn.signal" not in src, "runTurn 不能绕过重试逻辑直发"
    assert "if (failed) msg.failed = true;" in src
    assert "(m) => !(m.role === 'assistant' && m.failed)" in src, "失败条目不该喂回模型"
    assert "const timedOut = streamCause === 'timeout';" in src, "流超时不能说成「已停止」"
    # 状态文案必须告诉用户正在重试，否则瞬时失败看起来就是「点了没反应」
    assert "连接失败，正在重试（" in src
