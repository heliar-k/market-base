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
    ok = chat_proxy.validate_messages([("user", "hi")])
    assert ok == [{"role": "user", "content": "hi"}]

    for items, status in [
        ([], 422),
        ([("tool", "x")], 422),
        ([("assistant", "x")], 422),
        ([("user", "x" * (chat_proxy.MAX_CHARS + 1))], 413),
        ([("user", "x")] * (chat_proxy.MAX_MESSAGES + 1), 413),
    ]:
        try:
            chat_proxy.validate_messages(items)
        except chat_proxy.ChatValidationError as exc:
            assert exc.status == status
        else:  # pragma: no cover
            raise AssertionError(f"expected ChatValidationError for {items[:1]}...")


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
