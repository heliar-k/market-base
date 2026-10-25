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

    from src.fetchers import _wiki

    monkeypatch.setattr(_wiki.requests, "get", fake_get)


def test_components_parse(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "COMPONENTS_PATH", tmp_path / "ndx_components.csv")
    _mock_get(monkeypatch, WIKI_HTML)
    df = af.fetch_ndx_components()
    assert list(df["ticker"]) == ["ADBE", "AMD"]  # FOO 非法 ticker 被过滤
    assert df.loc[0, "company"] == "Adobe Inc."
    assert df.loc[1, "industry"] == "Technology"
    assert (tmp_path / "ndx_components.csv").exists()  # 缓存已写


WIKI_HTML_WITH_ISIN = """
<table class="wikitable sortable">
<tr><th>Ticker</th><th>Company</th><th>ISIN</th><th>ICB Industry</th>
<th>ICB Subsector</th></tr>
<tr><td>ADBE</td><td>Adobe Inc.</td><td>US00724F1012</td><td>Technology</td>
<td>Software</td></tr>
<tr><td>AMD</td><td>Advanced Micro Devices</td><td>US0079031078</td><td>Technology</td>
<td>Semiconductors</td></tr>
</table>
"""


def test_components_parse_skips_isin_column(monkeypatch, tmp_path):
    """行业列按表头定位，不把 ISIN 当行业（2026-10 Wikipedia 插了 ISIN 列）。"""
    monkeypatch.setattr(af, "COMPONENTS_PATH", tmp_path / "ndx_components.csv")
    _mock_get(monkeypatch, WIKI_HTML_WITH_ISIN)
    df = af.fetch_ndx_components()
    assert list(df["industry"]) == ["Technology", "Technology"]


def test_category_col_direct(monkeypatch):
    """表头定位的直接测试：合并标题行 / 无行业表头 fallback。"""
    from src.fetchers._wiki import _category_col

    # 合并标题行抢在表头前（wikitable 常见）仍能找到 industry 列
    titled = (
        '<tr><th colspan="5">Components</th></tr>'
        "<tr><th>Ticker</th><th>Company</th><th>ISIN</th><th>ICB Industry</th></tr>"
    )
    assert _category_col(titled) == 3
    # 无 industry/sector 表头 → 发出警告并回退旧假设
    warned = []
    monkeypatch.setattr("src.fetchers._wiki.logger.warning", warned.append)
    assert _category_col("<tr><th>A</th><th>B</th><th>C</th></tr>") == 2
    assert warned


def test_isin_in_category_dropped(monkeypatch, tmp_path):
    """fallback 取到 ISIN 时不把该行写进成分（宁可缺行也不污染行业）。"""
    broken = """
<table class="wikitable"><tr><th>Ticker</th><th>Company</th><th>Other</th></tr>
<tr><td>ADBE</td><td>Adobe</td><td>US00724F1012</td></tr>
<tr><td>AMD</td><td>AMD</td><td>Technology</td></tr></table>
"""
    monkeypatch.setattr(af, "COMPONENTS_PATH", tmp_path / "c.csv")
    _mock_get(monkeypatch, broken)
    df = af.fetch_ndx_components()
    assert list(df["ticker"]) == ["AMD"]  # ISIN 行被剔除


def test_components_fallback_to_cache(monkeypatch, tmp_path):
    cache = tmp_path / "ndx_components.csv"
    pd.DataFrame(
        {"ticker": ["AAPL"], "company": ["Apple Inc."], "industry": ["Technology"]}
    ).to_csv(cache, index=False)
    monkeypatch.setattr(af, "COMPONENTS_PATH", cache)
    _mock_get(monkeypatch, exc=RuntimeError("network down"))
    df = af.fetch_ndx_components()
    assert list(df["ticker"]) == ["AAPL"]  # 网络失败回退缓存


def test_targets_columns_single_industry_taxonomy(tmp_path):
    """落盘列只有一套行业口径（industry=wiki ICB），不再带 yfinance 的 sector。"""
    cols = [
        "date",
        "ticker",
        "company",
        "industry",
        "price",
        "target_mean",
        "target_high",
        "target_low",
        "upside",
        "analysts",
        "rating",
    ]
    df = pd.DataFrame({c: [None] for c in cols})
    from src.fetchers._io import upsert_rows

    p = tmp_path / "ndx_targets.csv"
    upsert_rows(p, df, subset=["date", "ticker"])
    got = pd.read_csv(p).columns.tolist()
    assert got == cols and "sector" not in got


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
    assert "sector" not in r  # 行业只认 wiki 的 industry，不收 yfinance 的第二套口径
    assert af._ticker_targets("NO_COVERAGE") is None  # 无覆盖 → 跳过
    with pytest.raises(RuntimeError):  # 限流异常向上抛，由 fetch_ndx_targets 循环捕获
        af._ticker_targets("LIMIT")
