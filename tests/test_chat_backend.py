"""伴读抽屉后端（/api/chat · /api/models · /api/notes）的本地行为测试。

不打真网络：上游用 fake response，落盘用 tmp_path；覆盖校验、白名单、限流、
跨站/超限拦截、文件名消毒与去重。Pages Functions 的同名契约见 test_pages_functions.py。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from src import chat_proxy, notes_store, server


class _Req:
    """够 _client_ip / route 用的最小 Request 桩。"""

    def __init__(self, ip: str = "127.0.0.1") -> None:
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host=ip)


class _GuardReq:
    def __init__(self, path: str, headers: dict[str, str], method: str = "POST") -> None:
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.headers = headers


async def _guard_next(_request):
    return JSONResponse({"ok": True})


def _body(messages=None, **kw):
    return server.ChatBodyIn(
        messages=messages or [server.ChatMessageIn(role="user", content="hi")], **kw
    )


def _tool(name: str = "query_site_data", **fn_kw) -> dict:
    """一个形状合法的工具定义（T1 的 query_site_data 精简版）。"""
    fn = {
        "name": name,
        "description": "查询本站已导出的数据端点。",
        "parameters": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
    }
    fn.update(fn_kw)
    return {"type": "function", "function": fn}


def _tool_call(
    call_id: str = "call_1", arguments: str = '{"endpoint":"rates/yield-curve"}'
) -> dict:
    """assistant 消息里的一个 tool_calls 项（OpenAI 协议形状）。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "query_site_data", "arguments": arguments},
    }


def _collect(resp) -> bytes:
    it = resp.body_iterator
    if hasattr(it, "__aiter__"):

        async def _run():
            return b"".join([chunk async for chunk in it])

        return asyncio.run(_run())
    return b"".join(it)


# ── 配置 / 模型白名单 ───────────────────────────────────────────────────────


def test_chat_models_default_and_env(monkeypatch) -> None:
    monkeypatch.delenv("LLM_CHAT_MODELS", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert chat_proxy.chat_models() == ["deepseek-flash"]

    monkeypatch.setenv("LLM_MODEL", "m1")
    assert chat_proxy.chat_models() == ["m1"]

    monkeypatch.setenv("LLM_CHAT_MODELS", "a, b ,,c")
    assert chat_proxy.chat_models() == ["a", "b", "c"]


def test_resolve_model_falls_back_to_whitelist(monkeypatch) -> None:
    monkeypatch.setenv("LLM_CHAT_MODELS", "a,b")
    assert chat_proxy.resolve_model("b") == "b"
    assert chat_proxy.resolve_model("evil") == "a"
    assert chat_proxy.resolve_model(None) == "a"


# ── 思考模式（deepseek-flash 默认开，抽屉只渲染 content 流必须钉住非思考）──


def test_thinking_mode_defaults_enabled_on_deepseek(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_THINKING", raising=False)
    assert chat_proxy.thinking_mode() == "enabled"

    monkeypatch.setenv("LLM_THINKING", "off")
    assert chat_proxy.thinking_mode() == "disabled"

    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    assert chat_proxy.thinking_mode() is None  # 别的网关不认 thinking 字段


def test_build_payload_sends_thinking_mode(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_CHAT_MAX_TOKENS", raising=False)
    payload = chat_proxy.build_payload(None, [{"role": "user", "content": "hi"}], None)
    assert payload["thinking"] == {"type": "enabled"}
    # 思考 token 计入 max_tokens：默认档必须比非思考高，否则正文被思维链吃光
    assert payload["max_tokens"] == chat_proxy.MAX_TOKENS_THINKING

    monkeypatch.setenv("LLM_THINKING", "off")
    payload = chat_proxy.build_payload(None, [{"role": "user", "content": "hi"}], None)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == chat_proxy.MAX_TOKENS

    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    payload = chat_proxy.build_payload(None, [{"role": "user", "content": "hi"}], None)
    assert "thinking" not in payload


def test_validate_messages(monkeypatch) -> None:
    ok = chat_proxy.validate_messages([{"role": "user", "content": "hi"}])
    assert ok == [{"role": "user", "content": "hi"}]

    for items, status in [
        ([], 422),
        ([{"role": "tool", "content": "x"}], 422),  # tool 角色缺 tool_call_id
        ([{"role": "assistant", "content": "x"}], 422),  # 没有 user
        ([{"role": "user", "content": "x" * (chat_proxy.MAX_CHARS + 1)}], 413),
        ([{"role": "user", "content": "x"}] * (chat_proxy.MAX_MESSAGES + 1), 413),
    ]:
        try:
            chat_proxy.validate_messages(items)
        except chat_proxy.ChatValidationError as exc:
            assert exc.status == status
        else:  # pragma: no cover
            raise AssertionError(f"expected ChatValidationError for {items[:1]}...")


# ── 工具契约（与 functions/_lib/common.js 逐条对齐，见 docs/ai-chat-backend.md）──


def test_validate_tools_shape_and_limits() -> None:
    tool = _tool()
    assert chat_proxy.validate_tools([tool]) == [tool]
    assert chat_proxy.validate_tools(None) is None
    assert chat_proxy.validate_tools([]) is None  # 空数组视同未传

    bad_cases = [
        [_tool()] * (chat_proxy.MAX_TOOLS + 1),  # 9 个 → 422
        ["not-an-object"],
        [{**tool, "type": "code"}],  # type 只准 function
        [{"type": "function"}],  # 缺 function 对象
        [_tool(name="bad name")],  # 名字带空格
        [_tool(name="x" * 65)],  # 超过 64 位
        [_tool(description="x" * (chat_proxy.TOOL_DESC_MAX + 1))],
        [_tool(description=123)],
        [_tool(parameters=[])],  # parameters 必须是对象
        [_tool(parameters=None)],
    ]
    for items in bad_cases:
        try:
            chat_proxy.validate_tools(items)
        except chat_proxy.ChatValidationError as exc:
            assert exc.status == 422, items
        else:  # pragma: no cover
            raise AssertionError(f"expected 422 for {items!r}")

    # parameters 的内容（JSON Schema 细节）不校验
    loose = _tool(parameters={"type": "object", "whatever": ["nonsense"]})
    assert chat_proxy.validate_tools([loose]) == [loose]


def test_validate_tool_choice_only_auto_none() -> None:
    assert chat_proxy.validate_tool_choice("auto") == "auto"
    assert chat_proxy.validate_tool_choice("none") == "none"
    assert chat_proxy.validate_tool_choice(None) is None

    for bad in ("required", "AUTO", "", {"type": "function", "function": {"name": "x"}}, 1):
        try:
            chat_proxy.validate_tool_choice(bad)
        except chat_proxy.ChatValidationError as exc:
            assert exc.status == 422, bad
        else:  # pragma: no cover
            raise AssertionError(f"expected 422 for {bad!r}")


def test_validate_messages_tool_role_and_passthrough() -> None:
    """tool 角色必须带 tool_call_id；assistant 的 tool_calls 原样保留。"""
    msgs = [
        {"role": "user", "content": "30Y 现在多少"},
        {"role": "assistant", "content": None, "tool_calls": [_tool_call()]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"current":4.812}'},
    ]
    out = chat_proxy.validate_messages(msgs)
    # assistant 只带工具调用时 content 是 null → 归一成 ""
    assert out[1]["content"] == ""
    # 不重排、不裁字段
    assert out[1]["tool_calls"] == [_tool_call()]
    assert out[2]["tool_call_id"] == "call_1"
    assert out[2]["content"] == '{"current":4.812}'

    bad_cases = [
        [{"role": "tool", "content": "x"}],  # 缺 tool_call_id
        [{"role": "tool", "tool_call_id": "  ", "content": "x"}],  # 空 id
        [
            {
                "role": "tool",
                "tool_call_id": "c" * (chat_proxy.TOOL_CALL_ID_MAX + 1),
                "content": "x",
            }
        ],
        [{"role": "assistant", "content": "", "tool_calls": [_tool_call()] * 9}],  # >8 条
        [{"role": "assistant", "content": "", "tool_calls": "nope"}],
        [{"role": "assistant", "content": "", "tool_calls": ["nope"]}],
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"arguments": {"a": 1}}}],
            }
        ],
    ]
    for body in bad_cases:
        items = [{"role": "user", "content": "hi"}, *body]
        try:
            chat_proxy.validate_messages(items)
        except chat_proxy.ChatValidationError as exc:
            assert exc.status == 422, body
        else:  # pragma: no cover
            raise AssertionError(f"expected 422 for {body!r}")


def test_validate_messages_counts_tool_arguments() -> None:
    """tool_calls 里的 arguments 也计入 40k 总字符（否则可以绕过）。"""
    room = chat_proxy.MAX_CHARS - len("hi")
    ok = chat_proxy.validate_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [_tool_call(arguments="x" * room)]},
        ]
    )
    assert ok

    over = [{"role": "user", "content": "hi"}]
    over.append(
        {"role": "assistant", "content": "", "tool_calls": [_tool_call(arguments="x" * (room + 1))]}
    )
    try:
        chat_proxy.validate_messages(over)
    except chat_proxy.ChatValidationError as exc:
        assert exc.status == 413
    else:  # pragma: no cover
        raise AssertionError("arguments 未计入字符上限")


def test_rate_limit_window(monkeypatch) -> None:
    key = "unit:rate-limit"
    server._CHAT_HITS.pop(key, None)
    assert server._rate_ok(key, 2, 60) is True
    assert server._rate_ok(key, 2, 60) is True
    assert server._rate_ok(key, 2, 60) is False
    server._CHAT_HITS.pop(key, None)


# ── 跨站 / body 上限中间件 ───────────────────────────────────────────────────


def test_guard_rejects_external_origin() -> None:
    req = _GuardReq("/api/chat", {"origin": "https://evil.example"})
    resp = asyncio.run(server._guard_api(req, _guard_next))
    assert resp.status_code == 403


def test_guard_allows_local_origin() -> None:
    req = _GuardReq("/api/chat", {"origin": "http://localhost:8000"})
    resp = asyncio.run(server._guard_api(req, _guard_next))
    assert resp.status_code == 200


def test_guard_rejects_oversize_and_chunked() -> None:
    too_big = _GuardReq(
        "/api/chat",
        {
            "origin": "http://127.0.0.1:8000",
            "content-length": str(chat_proxy.BODY_MAX + 1),
        },
    )
    assert asyncio.run(server._guard_api(too_big, _guard_next)).status_code == 413

    chunked = _GuardReq(
        "/api/chat",
        {"origin": "http://127.0.0.1:8000", "transfer-encoding": "chunked"},
    )
    assert asyncio.run(server._guard_api(chunked, _guard_next)).status_code == 411

    notes_big = _GuardReq(
        "/api/notes",
        {
            "origin": "http://127.0.0.1:8000",
            "content-length": str(notes_store.BODY_MAX + 1),
        },
    )
    assert asyncio.run(server._guard_api(notes_big, _guard_next)).status_code == 413


def test_is_local_origin() -> None:
    assert server._is_local_origin("") is True
    assert server._is_local_origin("http://localhost:8000") is True
    assert server._is_local_origin("http://127.0.0.1:4321") is True
    assert server._is_local_origin("http://[::1]:8000") is True
    assert server._is_local_origin("https://evil.example") is False
    assert server._is_local_origin("null") is False


# ── /api/models ─────────────────────────────────────────────────────────────


def test_models_requires_server_key(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    resp = server.get_chat_models(_Req("10.0.0.1"))
    assert resp.status_code == 503
    assert "LLM_API_KEY" in json.loads(resp.body)["error"]


def test_models_lists_whitelist(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_CHAT_MODELS", "m1,m2")
    out = server.get_chat_models(_Req("10.0.0.2"))
    assert out["proxy"] is True
    assert [m["id"] for m in out["data"]] == ["m1", "m2"]


def test_models_rate_limit(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k")
    ip = "10.0.0.3"
    for _ in range(30):
        assert isinstance(server.get_chat_models(_Req(ip)), dict)
    resp = server.get_chat_models(_Req(ip))
    assert resp.status_code == 429
    assert "30" in json.loads(resp.body)["error"]


# ── /api/chat ───────────────────────────────────────────────────────────────


def test_chat_requires_server_key(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    resp = server.chat_proxy(_body(), _Req("10.0.1.1"))
    assert resp.status_code == 503
    assert "LLM_API_KEY" in json.loads(resp.body)["error"]


def test_chat_rejects_bad_messages(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k")
    empty = server.chat_proxy(server.ChatBodyIn(messages=[]), _Req("10.0.1.2"))
    assert empty.status_code == 422

    bad_role = server.chat_proxy(
        server.ChatBodyIn(messages=[server.ChatMessageIn(role="tool", content="x")]),
        _Req("10.0.1.3"),
    )
    assert bad_role.status_code == 422

    huge = server.chat_proxy(
        server.ChatBodyIn(
            messages=[server.ChatMessageIn(role="user", content="x" * (chat_proxy.MAX_CHARS + 1))]
        ),
        _Req("10.0.1.4"),
    )
    assert huge.status_code == 413


def test_chat_streams_upstream_and_resolves_model(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "server-model")
    # .env 里若指了本地网关，断言默认 base 会失败——本用例钉的是「默认 base」这条
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_CHAT_ALLOW_EFFORT", raising=False)
    monkeypatch.delenv("LLM_CHAT_MAX_TOKENS", raising=False)
    captured: dict = {}

    class _FakeUpstream:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def __init__(self) -> None:
            self.closed = False

        def iter_content(self, chunk_size=None):
            yield b"data: one\n\n"
            yield b"data: [DONE]\n\n"

        def close(self) -> None:
            self.closed = True

    fake = _FakeUpstream()

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(server.requests, "post", _post)
    resp = server.chat_proxy(_body(model="client-model", reasoning_effort="high"), _Req("10.0.1.5"))

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-llm-model"] == "server-model"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    payload = captured["json"]
    assert payload["model"] == "server-model"  # 客户端不在白名单 → 服务端覆盖
    assert payload["stream"] is True
    assert payload["max_tokens"] == chat_proxy.MAX_TOKENS_THINKING
    assert "reasoning_effort" not in payload  # 默认不透传（避免不认的网关 400）
    assert "tools" not in payload and "tool_choice" not in payload  # 不带工具时不下发
    assert _collect(resp) == b"data: one\n\ndata: [DONE]\n\n"
    assert fake.closed is True


def test_chat_passes_effort_only_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_CHAT_ALLOW_EFFORT", "1")
    captured: dict = {}

    class _FakeUpstream:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def iter_content(self, chunk_size=None):
            yield b""

        def close(self) -> None:
            pass

    def _post(url, **kwargs):
        captured.update(kwargs)
        return _FakeUpstream()

    monkeypatch.setattr(server.requests, "post", _post)
    server.chat_proxy(_body(reasoning_effort="high"), _Req("10.0.1.6"))
    assert captured["json"]["reasoning_effort"] == "high"


def test_chat_returns_upstream_error(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k")

    class _Bad:
        status_code = 401
        headers = {"content-type": "application/json"}
        text = '{"error":"bad key"}'

        def close(self) -> None:
            pass

    monkeypatch.setattr(server.requests, "post", lambda url, **kw: _Bad())
    resp = server.chat_proxy(_body(), _Req("10.0.1.7"))
    assert resp.status_code == 401
    assert "上游 401" in json.loads(resp.body)["error"]


def test_chat_rejects_bad_tool_payload(monkeypatch) -> None:
    """三种畸形工具请求的状态码，与 test_pages_functions.py 的同一批断言对齐。"""
    monkeypatch.setenv("LLM_API_KEY", "k")
    cases = [
        _body(tools=[_tool()] * (chat_proxy.MAX_TOOLS + 1)),  # 9 个工具
        _body(tool_choice="required"),  # 思考模式下上游会 400
        _body(tool_choice={"type": "function", "function": {"name": "query_site_data"}}),
        _body(messages=[server.ChatMessageIn(role="tool", content="x")]),  # 缺 tool_call_id
        _body(tools=[_tool(parameters="not-an-object")]),
    ]
    for body in cases:
        resp = server.chat_proxy(body, _Req("10.0.3.1"))
        assert resp.status_code == 422, body
        assert json.loads(resp.body)["error"]  # 前端直接显示这句话


def test_chat_passes_tools_and_tool_messages_upstream(monkeypatch) -> None:
    """校验通过后 tools / tool_choice 原样进上游，tool_calls / tool_call_id 不被抹掉。"""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_CHAT_ALLOW_EFFORT", raising=False)
    captured: dict = {}

    class _FakeUpstream:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def iter_content(self, chunk_size=None):
            yield b"data: [DONE]\n\n"

        def close(self) -> None:
            pass

    def _post(url, **kwargs):
        captured.update(kwargs)
        return _FakeUpstream()

    monkeypatch.setattr(server.requests, "post", _post)
    tool_call = _tool_call()
    resp = server.chat_proxy(
        _body(
            messages=[
                server.ChatMessageIn(role="user", content="30Y 现在多少"),
                server.ChatMessageIn(role="assistant", tool_calls=[tool_call]),
                server.ChatMessageIn(
                    role="tool", tool_call_id="call_1", content='{"current":4.812}'
                ),
            ],
            tools=[_tool()],
            tool_choice="auto",
        ),
        _Req("10.0.3.2"),
    )
    assert resp.status_code == 200
    payload = captured["json"]
    assert payload["tools"] == [_tool()]
    assert payload["tool_choice"] == "auto"
    assert payload["messages"][1]["content"] == ""  # null → ""
    assert payload["messages"][1]["tool_calls"] == [tool_call]
    assert payload["messages"][2]["tool_call_id"] == "call_1"
    assert payload["messages"][2]["content"] == '{"current":4.812}'


# ── /api/notes ──────────────────────────────────────────────────────────────


def test_note_filename_sanitizes() -> None:
    assert notes_store.note_filename("../../etc/passwd") == "passwd.md"
    assert notes_store.note_filename("a/b/c.md") == "c.md"
    assert notes_store.note_filename("..", "伴读对话") == "伴读对话.md"
    assert notes_store.note_filename("") == "note.md"
    assert notes_store.note_filename('a:b*c?"d"') == "a-b-c--d.md"
    long = notes_store.note_filename("长" * 200)
    assert long.endswith(".md") and len(long) <= 120


def test_note_path_dedupes(tmp_path) -> None:
    first = notes_store.free_note_path("x.md", tmp_path)
    first.write_text("a", encoding="utf-8")
    second = notes_store.free_note_path("x.md", tmp_path)
    assert second.name == "x-2.md"


def test_save_note_writes_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(notes_store, "NOTES_DIR", tmp_path)
    monkeypatch.setattr(server, "_rate_ok", lambda *a, **k: True)
    resp = server.save_note(
        server.NoteBodyIn(filename="../../etc/passwd", markdown="# 标题\n正文"),
        _Req("10.0.2.1"),
    )
    assert resp["ok"] is True
    path = Path(resp["path"])
    assert path.name == "passwd.md"
    assert path.read_text(encoding="utf-8") == "# 标题\n正文"


def test_save_note_uses_thread_title(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(notes_store, "NOTES_DIR", tmp_path)
    monkeypatch.setattr(server, "_rate_ok", lambda *a, **k: True)
    resp = server.save_note(
        server.NoteBodyIn(markdown="x", thread={"title": "利率复盘"}), _Req("10.0.2.2")
    )
    assert Path(resp["path"]).name == "利率复盘.md"


def test_save_note_rejects_empty_and_oversize(monkeypatch) -> None:
    monkeypatch.setattr(server, "_rate_ok", lambda *a, **k: True)
    empty = server.save_note(server.NoteBodyIn(markdown="   "), _Req("10.0.2.3"))
    assert empty.status_code == 400
    huge = server.save_note(
        server.NoteBodyIn(markdown="x" * (notes_store.MAX_BYTES + 1)), _Req("10.0.2.4")
    )
    assert huge.status_code == 413
