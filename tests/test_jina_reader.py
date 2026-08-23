"""Jina Reader 通用模块单元测试。"""

from src.fetchers.jina_reader import jina_fetch


class _Resp:
    def __init__(self, headers: dict, text: str):
        self.headers = headers
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self):
        pass


def test_json_content_unwrap(monkeypatch):
    """Accept: application/json → 解包 data.content。"""
    import requests

    def fake_get(url, **kw):
        assert url == "https://r.jina.ai/https://example.com"
        assert kw["headers"]["Accept"] == "application/json"
        return _Resp(
            {"content-type": "application/json"},
            '{"data": {"content": "hello markdown"}}',
        )

    monkeypatch.setattr(requests, "get", fake_get)
    assert jina_fetch("https://example.com") == "hello markdown"


def test_html_passthrough(monkeypatch):
    import requests

    def fake_get(url, **kw):
        return _Resp({"content-type": "text/plain"}, "plain text")

    monkeypatch.setattr(requests, "get", fake_get)
    assert jina_fetch("https://example.com") == "plain text"


def test_proxy_from_env(monkeypatch):
    """HTTPS_PROXY 存在 → 转发给 requests（本地需代理，Actions 无）。"""
    import requests

    seen = {}

    def fake_get(url, **kw):
        seen.update(kw.get("proxies") or {})
        return _Resp({"content-type": "text/plain"}, "ok")

    monkeypatch.setenv("HTTPS_PROXY", "socks5h://127.0.0.1:7890")
    monkeypatch.setattr(requests, "get", fake_get)
    jina_fetch("https://example.com")
    assert seen.get("https") == "socks5h://127.0.0.1:7890"


def test_json_missing_content_raises(monkeypatch):
    import pytest
    import requests

    def fake_get(url, **kw):
        return _Resp({"content-type": "application/json"}, '{"data": {}}')

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(RuntimeError):
        jina_fetch("https://example.com")
