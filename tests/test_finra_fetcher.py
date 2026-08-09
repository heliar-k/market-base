"""finra_fetcher 解析/降级逻辑测试（样例文本，无网络）。"""

import pandas as pd

from src.fetchers import finra_fetcher

_SAMPLE = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260807|AAPL|5540409.463985|21|13331568.55|B,Q,N
20260807|TSM|1527824.096894|5|4058600.5|B,Q,N
20260807|ZZZZ|100|0|500|B,Q,N
20260806|AAPL|5000000|15|12000000|B,Q,N
"""


def test_parse_filters_and_computes_ratio():
    """非 watchlist 符号剔除；ratio = Short/Total；小数成交量原样保留。"""
    df = finra_fetcher._parse(_SAMPLE, {"AAPL", "TSM"})
    assert list(df.columns) == [
        "AAPL_short_ratio",
        "TSM_short_ratio",
        "AAPL_short_vol",
        "TSM_short_vol",
    ]
    assert list(df.index) == ["2026-08-06", "2026-08-07"]
    assert df.loc["2026-08-07", "AAPL_short_vol"] == 5540409.463985
    expected = 5540409.463985 / 13331568.55
    assert abs(df.loc["2026-08-07", "AAPL_short_ratio"] - expected) < 1e-12
    assert "ZZZZ" not in " ".join(df.columns)


def test_parse_empty_when_no_watchlist_match():
    assert finra_fetcher._parse(_SAMPLE, {"NOPE"}).empty


def test_fetch_skips_missing_dates(monkeypatch):
    """404/失败日静默跳过，全跳过时返回空 DataFrame。"""
    monkeypatch.setattr(finra_fetcher, "_get", lambda url: None)
    from datetime import date

    assert finra_fetcher.fetch_finra([date(2026, 8, 8)], {"AAPL"}).empty


def test_backfill_dates_anchors_on_trading_day(monkeypatch):
    """今天为周末时：先锚定最近交易日再步进，连续 2 桶无文件才截断。"""
    from datetime import date, timedelta

    # 2026-08-09 是周日；文件存在于 2026-08-07（周五）回溯 49 天（7 桶）内
    anchor = date(2026, 8, 7)
    oldest_ok = anchor - timedelta(days=49)

    def fake_exists(d):
        return oldest_ok <= d <= anchor

    monkeypatch.setattr(finra_fetcher, "_exists", fake_exists)
    dates = finra_fetcher.backfill_dates()
    assert dates[0] == oldest_ok and dates[-1] == anchor  # 从锚点往回填
    assert len(dates) == 50


def test_backfill_dates_returns_empty_without_any_file(monkeypatch):
    monkeypatch.setattr(finra_fetcher, "_exists", lambda d: False)
    assert finra_fetcher.backfill_dates() == []


def test_get_falls_back_to_proxy(monkeypatch):
    """直连异常 → 代理成功；失败记忆生效后不再尝试直连。"""

    class _Resp:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

        def close(self):
            pass

    calls = []

    def fake_request(
        method, url, proxies=None, timeout=30, allow_redirects=False, stream=True
    ):
        calls.append(proxies)
        if proxies is None:
            raise finra_fetcher.requests.RequestException("direct blocked")
        if url.endswith("404.txt"):
            return _Resp(403)  # cdn.finra.org 缺失文件实测返回 403
        return _Resp(200, _SAMPLE)

    monkeypatch.setattr(finra_fetcher, "_DIRECT_OK", True)
    monkeypatch.setattr(finra_fetcher._SESSION, "request", fake_request)
    assert finra_fetcher._get("https://x/200.txt") == _SAMPLE
    assert finra_fetcher._get("https://x/404.txt") is None
    # 第一次直连失败后 _DIRECT_OK=False：后续请求（缺失探测）不再尝试直连
    assert finra_fetcher._DIRECT_OK is False
    proxy = {"https": finra_fetcher._PROXY_URL, "http": finra_fetcher._PROXY_URL}
    assert calls == [None, proxy, proxy]


def test_get_direct_307_marks_blocked(monkeypatch):
    """直连被 WAF 307 重定向 → 记忆为不可直连，后续只用代理。"""

    class _Resp:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

        def close(self):
            pass

    calls = []

    def fake_request(
        method, url, proxies=None, timeout=30, allow_redirects=False, stream=True
    ):
        calls.append(proxies)
        if proxies is None:
            return _Resp(307)
        return _Resp(200, _SAMPLE)

    monkeypatch.setattr(finra_fetcher, "_DIRECT_OK", True)
    monkeypatch.setattr(finra_fetcher._SESSION, "request", fake_request)
    assert finra_fetcher._get("https://x/a.txt") == _SAMPLE
    assert finra_fetcher._get("https://x/b.txt") == _SAMPLE
    proxy = {"https": finra_fetcher._PROXY_URL, "http": finra_fetcher._PROXY_URL}
    assert calls == [None, proxy, proxy]  # 第二次请求不再走直连
    assert finra_fetcher._DIRECT_OK is False


def test_fetch_upsert_roundtrip(tmp_path):
    """fetch 结果可被 upsert_timeseries 落盘并读回。"""
    df = finra_fetcher._parse(_SAMPLE, {"AAPL"})
    path = tmp_path / "finra_daily.csv"
    finra_fetcher.upsert_timeseries(path, df)
    back = pd.read_csv(path, index_col="date")
    assert back.loc["2026-08-07", "AAPL_short_vol"] == 5540409.463985
