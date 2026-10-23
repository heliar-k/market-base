"""Cloudflare Pages Functions（functions/api/*.js）的契约测试。

用 node 直接 import 处理器、喂内存 Request / 假 fetch / 假 KV——不需要 wrangler、
不连网。路由是否被 Pages 调用由 _routes.json 决定（另有断言）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.node_runner import node_check, run_node

ROOT = Path(__file__).resolve().parent.parent
FUNCS = ROOT / "functions"

JS = r"""
const base = __BASE__;
const chat = await import(base + 'api/chat.js');
const models = await import(base + 'api/models.js');
const notes = await import(base + 'api/notes.js');
const common = await import(base + '_lib/common.js');
const out = {};

let captured = null;
globalThis.fetch = async (url, init) => {
  captured = { url: String(url), init };
  return new Response('data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
};

const req = (path, body, ip) =>
  new Request('https://site.test' + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': ip },
    body: JSON.stringify(body),
  });

// ── 基本代理 + 模型覆盖 + x-llm-model ───────────────────────────────────────
const first = await chat.onRequestPost({
  request: req('/api/chat', { messages: [{ role: 'user', content: 'hi' }], model: 'bad' }, __IP1__),
  env: { LLM_API_KEY: 'k', LLM_MODEL: 'm1' },
});
out.chat = {
  status: first.status,
  model: first.headers.get('x-llm-model'),
  text: await first.text(),
  url: captured.url,
  payload: JSON.parse(captured.init.body),
};

// ── 缺 Key / 超条数 ────────────────────────────────────────────────────────
const noKey = await chat.onRequestPost({
  request: req('/api/chat', { messages: [{ role: 'user', content: 'hi' }] }, __IP2__),
  env: {},
});
out.noKey = { status: noKey.status, body: await noKey.json() };

const many = await chat.onRequestPost({
  request: req(
    '/api/chat',
    { messages: Array.from({ length: 31 }, () => ({ role: 'user', content: 'x' })) },
    __IP3__
  ),
  env: { LLM_API_KEY: 'k' },
});
out.tooMany = { status: many.status, body: await many.json() };

// ── 40k 中文字符（≈120KB UTF-8）不应被 256KB 的 body 上限误杀 ───────────────
const cjk = '中'.repeat(40000);
const cjkRes = await chat.onRequestPost({
  request: req('/api/chat', { messages: [{ role: 'user', content: cjk }] }, __IP9__),
  env: { LLM_API_KEY: 'k' },
});
out.cjk = { status: cjkRes.status, bytes: new TextEncoder().encode(cjk).length };
await cjkRes.text();

// ── 工具通道：与 test_chat_backend.py 同一批畸形请求（状态码必须一一对应）────
const TOOL = {
  type: 'function',
  function: {
    name: 'query_site_data',
    description: '查询本站已导出的数据端点。',
    parameters: { type: 'object', properties: { endpoint: { type: 'string' } } },
  },
};
const TOOL_CALL = {
  id: 'call_1',
  type: 'function',
  function: { name: 'query_site_data', arguments: '{"endpoint":"rates/yield-curve"}' },
};
const USER = { role: 'user', content: 'hi' };

const nineTools = await chat.onRequestPost({
  request: req('/api/chat', { messages: [USER], tools: Array(9).fill(TOOL) }, __IP14__),
  env: { LLM_API_KEY: 'k' },
});
out.tooManyTools = { status: nineTools.status, body: await nineTools.json() };

const requiredChoice = await chat.onRequestPost({
  request: req('/api/chat', { messages: [USER], tool_choice: 'required' }, __IP15__),
  env: { LLM_API_KEY: 'k' },
});
out.requiredChoice = { status: requiredChoice.status, body: await requiredChoice.json() };

const namedChoice = await chat.onRequestPost({
  request: req(
    '/api/chat',
    {
      messages: [USER],
      tool_choice: { type: 'function', function: { name: 'query_site_data' } },
    },
    __IP16__
  ),
  env: { LLM_API_KEY: 'k' },
});
out.namedChoice = { status: namedChoice.status, body: await namedChoice.json() };

const toolNoId = await chat.onRequestPost({
  request: req(
    '/api/chat',
    {
      messages: [
        USER,
        { role: 'assistant', content: null, tool_calls: [TOOL_CALL] },
        { role: 'tool', content: 'x' },
      ],
    },
    __IP17__
  ),
  env: { LLM_API_KEY: 'k' },
});
out.toolNoId = { status: toolNoId.status, body: await toolNoId.json() };

// 合法往返：tools / tool_choice 原样进上游 payload，tool_calls / tool_call_id 不被抹掉
const okTools = await chat.onRequestPost({
  request: req(
    '/api/chat',
    {
      messages: [
        { role: 'user', content: '30Y 现在多少' },
        { role: 'assistant', content: null, tool_calls: [TOOL_CALL] },
        { role: 'tool', tool_call_id: 'call_1', content: '{"current":4.812}' },
      ],
      tools: [TOOL],
      tool_choice: 'auto',
    },
    __IP18__
  ),
  env: { LLM_API_KEY: 'k' },
});
out.toolsOk = { status: okTools.status, payload: JSON.parse(captured.init.body) };
await okTools.text();

// arguments 计入 40k 总字符：'hi'(2) + 39998 刚好卡线，多 1 字就 413
const budget = async (argLen, ip) => {
  const r = await chat.onRequestPost({
    request: req(
      '/api/chat',
      {
        messages: [
          { role: 'user', content: 'hi' },
          {
            role: 'assistant',
            content: '',
            tool_calls: [
              {
                id: 'c1',
                type: 'function',
                function: { name: 'query_site_data', arguments: 'x'.repeat(argLen) },
              },
            ],
          },
        ],
      },
      ip
    ),
    env: { LLM_API_KEY: 'k' },
  });
  await r.text();
  return r.status;
};
out.argsOk = await budget(39998, __IP19__);
out.argsOver = await budget(39999, __IP20__);

// ── models ─────────────────────────────────────────────────────────────────
const list = await models.onRequestGet({
  request: new Request('https://site.test/api/models', {
    headers: { 'cf-connecting-ip': __IP4__ },
  }),
  env: { LLM_API_KEY: 'k', LLM_CHAT_MODELS: 'm1,m2' },
});
out.models = { status: list.status, body: await list.json() };

const noKeyModels = await models.onRequestGet({
  request: new Request('https://site.test/api/models', {
    headers: { 'cf-connecting-ip': __IP5__ },
  }),
  env: {},
});
out.noKeyModels = { status: noKeyModels.status, body: await noKeyModels.json() };

// ── notes：KV 写入 / 未绑 / 空 / 超过 1MB 字节 ──────────────────────────────
let put = null;
const note = await notes.onRequestPost({
  request: req(
    '/api/notes',
    { filename: '../../x', markdown: '# hi', thread: { title: 't' } },
    __IP6__
  ),
  env: { NOTES: { put: async (k, v, o) => { put = { k, v, o }; } } },
});
out.notes = { status: note.status, body: await note.json(), put };

const noKv = await notes.onRequestPost({
  request: req('/api/notes', { markdown: 'x' }, __IP7__),
  env: {},
});
out.noKv = { status: noKv.status, body: await noKv.json() };

const empty = await notes.onRequestPost({
  request: req('/api/notes', { markdown: '  ' }, __IP8__),
  env: { NOTES: { put: async () => {} } },
});
out.empty = { status: empty.status, body: await empty.json() };

const big = await notes.onRequestPost({
  request: req('/api/notes', { markdown: 'x'.repeat(1000001) }, __IP10__),
  env: { NOTES: { put: async () => {} } },
});
out.notesBig = { status: big.status, body: await big.json() };

// ── 429：三个入口各打满一次窗口 ────────────────────────────────────────────
let chatCodes = [];
for (let i = 0; i < 11; i++) {
  const r = await chat.onRequestPost({
    request: req('/api/chat', { messages: [{ role: 'user', content: 'hi' }] }, __IP11__),
    env: { LLM_API_KEY: 'k' },
  });
  chatCodes.push(r.status);
  await r.text();
}
out.chatRate = chatCodes;

let modelCodes = [];
for (let i = 0; i < 31; i++) {
  const r = await models.onRequestGet({
    request: new Request('https://site.test/api/models', {
      headers: { 'cf-connecting-ip': __IP12__ },
    }),
    env: { LLM_API_KEY: 'k', LLM_CHAT_MODELS: 'm1' },
  });
  modelCodes.push(r.status);
}
out.modelsRate = modelCodes;

let noteCodes = [];
for (let i = 0; i < 21; i++) {
  const r = await notes.onRequestPost({
    request: req('/api/notes', { markdown: 'x' }, __IP13__),
    env: { NOTES: { put: async () => {} } },
  });
  noteCodes.push(r.status);
  await r.text();
}
out.notesRate = noteCodes;
out.noteName = common.safeNoteName('a:b*c?"d"');

console.log(JSON.stringify(out));
"""


def _run() -> dict:
    script = JS.replace("__BASE__", json.dumps(FUNCS.as_uri() + "/"))
    for i in range(1, 21):
        script = script.replace(f"__IP{i}__", json.dumps(f"10.1.0.{i}"))
    return run_node(script)


@pytest.fixture(scope="module")
def out() -> dict:
    return _run()


def test_chat_proxies_and_resolves_model(out: dict) -> None:
    chat = out["chat"]
    assert chat["status"] == 200
    assert chat["model"] == "m1"
    assert "data:" in chat["text"]
    assert chat["url"] == "https://api.deepseek.com/chat/completions"
    assert chat["payload"]["model"] == "m1"  # 客户端模型不在白名单 → 服务端覆盖
    assert chat["payload"]["stream"] is True
    # 思考 token 计入 max_tokens，默认档随思考模式抬高
    assert chat["payload"]["max_tokens"] == 4000
    assert "reasoning_effort" not in chat["payload"]
    # 思考模式默认开启，代理显式下发，不依赖上游默认值
    assert chat["payload"]["thinking"] == {"type": "enabled"}


def test_chat_requires_key_and_limits(out: dict) -> None:
    assert out["noKey"]["status"] == 503
    assert "LLM_API_KEY" in out["noKey"]["body"]["error"]
    assert out["tooMany"]["status"] == 413


def test_cjk_40k_chars_passes_256kb_body_cap(out: dict) -> None:
    assert out["cjk"]["bytes"] > 64 * 1024  # 证明确实超过旧的 64KB 限制
    assert out["cjk"]["status"] == 200


def test_models_endpoint(out: dict) -> None:
    assert out["models"]["status"] == 200
    assert out["models"]["body"]["proxy"] is True
    assert [m["id"] for m in out["models"]["body"]["data"]] == ["m1", "m2"]
    assert out["noKeyModels"]["status"] == 503


def test_notes_writes_kv_and_sanitizes(out: dict) -> None:
    assert out["notes"]["status"] == 200
    assert out["notes"]["body"]["ok"] is True
    put = out["notes"]["put"]
    assert put["k"].startswith("notes/")
    assert put["k"].endswith("x.md")
    assert put["v"] == "# hi"
    assert out["noKv"]["status"] == 503
    assert "data/" in out["noKv"]["body"]["error"]
    assert out["empty"]["status"] == 400
    assert out["notesBig"]["status"] == 413
    assert out["noteName"] == "a-b-c--d.md"


def test_rate_limits(out: dict) -> None:
    assert out["chatRate"][0] == 200
    assert out["chatRate"][-1] == 429
    assert out["modelsRate"][0] == 200
    assert out["modelsRate"][-1] == 429
    assert out["notesRate"][0] == 200
    assert out["notesRate"][-1] == 429


def test_tools_contract_status_matches_python(out: dict) -> None:
    """验收标准第 10 条：同一批畸形请求，两侧状态码一致（见 test_chat_backend.py）。"""
    for key in ("tooManyTools", "requiredChoice", "namedChoice", "toolNoId"):
        assert out[key]["status"] == 422, key
        assert out[key]["body"]["error"]  # 前端直接显示这句话


def test_tools_pass_through_upstream_payload(out: dict) -> None:
    assert out["toolsOk"]["status"] == 200
    payload = out["toolsOk"]["payload"]
    assert payload["tool_choice"] == "auto"
    # tools 原样透传（name / description / parameters 一个字段都不裁）
    tool = payload["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "query_site_data"
    assert tool["function"]["description"] == "查询本站已导出的数据端点。"
    assert tool["function"]["parameters"] == {
        "type": "object",
        "properties": {"endpoint": {"type": "string"}},
    }
    # assistant 只带工具调用（content: null）→ 归一成 ""，tool_calls 原样保留
    assert payload["messages"][1]["content"] == ""
    assert payload["messages"][1]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "query_site_data",
                "arguments": '{"endpoint":"rates/yield-curve"}',
            },
        }
    ]
    assert payload["messages"][2]["tool_call_id"] == "call_1"


def test_tool_arguments_count_toward_char_budget(out: dict) -> None:
    assert out["argsOk"] == 200  # 'hi' + 39998 = 40000，刚好卡线
    assert out["argsOver"] == 413  # 多 1 字即超限（arguments 不能绕过 40k）


def test_routes_json_covers_functions() -> None:
    routes = json.loads((ROOT / "frontend" / "public" / "_routes.json").read_text(encoding="utf-8"))
    assert set(routes["include"]) == {"/api/chat", "/api/models", "/api/notes"}


def test_functions_parse() -> None:
    for path in sorted(FUNCS.rglob("*.js")):
        node_check(path)
