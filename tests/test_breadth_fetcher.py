"""breadth_fetcher 测试：成分解析 / compute_abv 纯函数 / 批量下载列提取。"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from src.fetchers import breadth_fetcher as bf

WIKI_HTML = """
<table class="wikitable sortable">
<tr><th>Symbol</th><th>Company</th><th>GICS Sector</th></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>
<tr><td>MSFT</td><td>Microsoft</td><td>Information Technology</td></tr>
<tr><td>BAD NAME</td><td>Broken Ticker!</td><td>Energy</td></tr>
</table>
"""


def _prices(n: int = 250) -> pd.DataFrame:
    k = np.arange(n)
    up = pd.Series(100 * np.cumprod(1 + 0.001 + 0.0005 * np.sin(k / 7)))
    down = pd.Series(100 * np.cumprod(1 - 0.001 + 0.0005 * np.sin(k / 7)))
    flat = pd.Series(100 + 0.5 * np.sin(k / 3))
    idx = pd.date_range("2025-01-01", periods=n)
    return pd.concat([up, down, flat], axis=1, keys=["UP", "DOWN", "FLAT"]).set_axis(
        idx
    )


def test_components_parse(monkeypatch, tmp_path):
    def fake_get(url, timeout=30, headers=None):
        r = MagicMock()
        r.text = WIKI_HTML
        return r

    monkeypatch.setattr(bf, "COMPONENTS_PATH", tmp_path / "sp500_components.csv")
    monkeypatch.setattr("src.fetchers._wiki.requests.get", fake_get)
    df = bf.fetch_sp500_components()
    assert list(df["ticker"]) == ["AAPL", "MSFT"]
    assert df.loc[0, "category"] == "Information Technology"


def test_components_fallback(monkeypatch, tmp_path):
    cache = tmp_path / "sp500_components.csv"
    pd.DataFrame({"ticker": ["AAPL"], "company": ["Apple"], "category": ["IT"]}).to_csv(
        cache, index=False
    )
    monkeypatch.setattr(bf, "COMPONENTS_PATH", cache)
    monkeypatch.setattr(
        "src.fetchers._wiki.requests.get",
        MagicMock(side_effect=RuntimeError()),
    )
    df = bf.fetch_sp500_components()
    assert list(df["ticker"]) == ["AAPL"]


def test_compute_abv():
    abv = bf.compute_abv(_prices())
    assert list(abv.columns) == ["ABV50", "ABV100", "ABV200"]
    last = abv.iloc[-1]
    # UP 在上方 + DOWN 在下方 + FLAT 末尾在上方 → 2/3
    assert abs(last["ABV200"] - 200 / 3) < 5
    # 前 200 天 ABV200 为 NaN（均线未满窗口）
    assert abv["ABV200"].iloc[:199].isna().all()
    # 占比范围 [0, 100]
    assert abv["ABV200"].dropna().between(0, 100).all()


def test_download_closes_extracts_close(monkeypatch, tmp_path):
    """MultiIndex (Price, Ticker) → 宽表 close。"""
    monkeypatch.setattr(bf, "COMPONENTS_PATH", tmp_path / "c.csv")
    idx = pd.date_range("2026-01-01", periods=3)
    raw = pd.DataFrame(
        {
            ("Close", "AAPL"): [1.0, 2.0, 3.0],
            ("Volume", "AAPL"): [100, 200, 300],
            ("Close", "MSFT"): [4.0, 5.0, 6.0],
        },
        index=idx,
    )
    raw.columns = pd.MultiIndex.from_tuples(raw.columns)
    fake_yf = MagicMock()
    fake_yf.download.return_value = raw
    import sys

    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    from src.fetchers import yfinance_fetcher

    monkeypatch.setattr(yfinance_fetcher, "ensure_yf_proxy", lambda: None)

    closes = bf.download_closes(["AAPL", "MSFT"])
    assert list(closes.columns) == ["AAPL", "MSFT"]
    assert closes["AAPL"].iloc[-1] == 3.0
    assert closes.index.name == "date"
