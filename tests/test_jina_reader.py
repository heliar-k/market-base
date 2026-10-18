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


def _status_resp(status: int, text: str = ""):
    class _S(_Resp):
        status_code = status

        def raise_for_status(self):
            import requests

            if status >= 400:
                raise requests.HTTPError(f"{status}")

    return _S({"content-type": "text/plain"}, text)


def test_retry_on_403_then_success(monkeypatch):
    """匿名档限流 403 → 按退避等待后重试成功。"""
    import requests

    from src.fetchers import jina_reader

    sleeps: list[int] = []
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return _status_resp(403) if len(calls) == 1 else _status_resp(200, "ok")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(jina_reader.time, "sleep", sleeps.append)
    assert jina_fetch("https://example.com") == "ok"
    assert len(calls) == 2
    assert sleeps == [jina_reader._RETRY_BACKOFF[0]]


def test_retry_gives_up_after_backoff(monkeypatch):
    """一直 403 → 重试次数用尽后抛错，不无限重试。"""
    import pytest
    import requests

    from src.fetchers import jina_reader

    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return _status_resp(403)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(jina_reader.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        jina_fetch("https://example.com")
    assert len(calls) == len(jina_reader._RETRY_BACKOFF) + 1


def test_json_missing_content_raises(monkeypatch):
    import pytest
    import requests

    def fake_get(url, **kw):
        return _Resp({"content-type": "application/json"}, '{"data": {}}')

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(RuntimeError):
        jina_fetch("https://example.com")
