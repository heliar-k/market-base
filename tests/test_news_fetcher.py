"""News fetcher 测试：NCP 响应解析（嵌套 content 字段）+ 广告过滤。"""

from src.fetchers import news_fetcher


def test_parse_stream_filters_ad_and_extracts_content(monkeypatch):
    """解析用 content.title（yfinance 顶层 title 已过时），广告条目剔除。"""
    stream = [
        {"ad": True, "content": {"title": "广告", "pubDate": ""}},
        {
            "content": {
                "title": "TSMC Q2 超预期",
                "summary": "净利 +77%",
                "pubDate": "2026-07-16T00:00:00Z",
                "canonicalUrl": {"url": "https://example.com/1"},
            }
        },
        {"content": {"title": "无 summary 条目"}},
    ]

    class FakeResp:
        def json(self):
            return {"data": {"tickerStream": {"stream": stream}}}

    class FakeSession:
        proxies: dict = {}
        post_calls = 0

        def post(self, url, json, timeout):
            self.post_calls += 1
            return FakeResp()

    monkeypatch.setattr(news_fetcher, "_get", lambda s, u: None)
    fake_cffi = type("FakeCffi", (), {"Session": lambda *a, **k: FakeSession()})()
    monkeypatch.setattr(news_fetcher, "cffi_requests", fake_cffi)
    items = news_fetcher.fetch_news("TSM", 3)
    assert len(items) == 2
    assert items[0].title == "TSMC Q2 超预期"
    assert items[0].summary == "净利 +77%"
    assert items[0].link == "https://example.com/1"
    assert items[1].title == "无 summary 条目"
    assert items[1].link == ""
