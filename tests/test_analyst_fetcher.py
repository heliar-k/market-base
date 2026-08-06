"""analyst_fetcher 测试：Wikipedia 成分解析 / 缓存回退 / 目标价过滤。"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.fetchers import analyst_fetcher as af

WIKI_HTML = """
<table class="wikitable sortable">
<tr><th>Ticker</th><th>Company</th><th>ICB Industry</th>
<th>ICB Subsector</th></tr>
<tr><td>ADBE</td><td>Adobe Inc.</td><td>Technology</td><td>Software</td></tr>
<tr><td>AMD</td><td>Advanced Micro Devices</td><td>Technology</td>
<td>Semiconductors</td></tr>
<tr><td>FOO BAR</td><td>Invalid!</td><td>Technology</td><td>x</td></tr>
</table>
"""


def _mock_get(monkeypatch, text=None, exc=None):
    def fake_get(url, timeout=30, headers=None):
        if exc:
            raise exc
        r = MagicMock()
        r.text = text or ""
        return r

    monkeypatch.setattr(af.requests, "get", fake_get)


def test_components_parse(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "COMPONENTS_PATH", tmp_path / "ndx_components.csv")
    _mock_get(monkeypatch, WIKI_HTML)
    df = af.fetch_ndx_components()
    assert list(df["ticker"]) == ["ADBE", "AMD"]  # FOO 非法 ticker 被过滤
    assert df.loc[0, "company"] == "Adobe Inc."
    assert df.loc[1, "industry"] == "Technology"
    assert (tmp_path / "ndx_components.csv").exists()  # 缓存已写


def test_components_fallback_to_cache(monkeypatch, tmp_path):
    cache = tmp_path / "ndx_components.csv"
    pd.DataFrame(
        {"ticker": ["AAPL"], "company": ["Apple Inc."], "industry": ["Technology"]}
    ).to_csv(cache, index=False)
    monkeypatch.setattr(af, "COMPONENTS_PATH", cache)
    _mock_get(monkeypatch, exc=RuntimeError("network down"))
    df = af.fetch_ndx_components()
    assert list(df["ticker"]) == ["AAPL"]  # 网络失败回退缓存


def test_ticker_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "COMPONENTS_PATH", tmp_path / "c.csv")

    class FakeInfo:
        def __init__(self, info):
            self.info = info

    def fake_ticker(ticker):
        if ticker == "WITH_TARGET":
            return FakeInfo(
                {
                    "targetMeanPrice": 200.0,
                    "targetHighPrice": 250.0,
                    "targetLowPrice": 150.0,
                    "numberOfAnalystOpinions": 30,
                    "recommendationKey": "buy",
                    "currentPrice": 180.0,
                }
            )
        if ticker == "NO_COVERAGE":
            return FakeInfo({"targetMeanPrice": None})
        raise RuntimeError("rate limited")

    import sys

    from src.fetchers import yfinance_fetcher

    monkeypatch.setattr(yfinance_fetcher, "ensure_yf_proxy", lambda: None)
    # _ticker_targets 内 import yfinance 走注入的 mock（monkeypatch teardown 自动恢复）
    monkeypatch.setitem(sys.modules, "yfinance", MagicMock(Ticker=fake_ticker))
    r = af._ticker_targets("WITH_TARGET")
    assert r["target_mean"] == 200.0 and r["analysts"] == 30
    assert af._ticker_targets("NO_COVERAGE") is None  # 无覆盖 → 跳过
    with pytest.raises(RuntimeError):  # 限流异常向上抛，由 fetch_ndx_targets 循环捕获
        af._ticker_targets("LIMIT")
