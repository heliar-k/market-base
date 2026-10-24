"""euro_sovereign_fetcher 单元测试：CNBC priceBars 解析 + 周末行过滤。

回归哨兵：CNBC 把周末写成周五收盘值的拷贝（6M 窗口 942 根里 161 根），
不过滤的话周六日各算一个"交易日"，任何均值/分位/相关性都会被三倍计权。
"""

import pandas as pd

from src.fetchers.euro_sovereign_fetcher import fetch_euro_yields


def _payload(*rows: tuple[str, str]) -> dict:
    return {"barData": {"priceBars": [{"tradeTime": t + "000000", "close": c} for t, c in rows]}}


# 2026-10-23 是周五，24/25 是周末（CNBC 会补两行周五收盘的拷贝）
BARS = {
    "DE10Y-DE": _payload(
        ("20261022", "3.3365"),
        ("20261023", "3.3429"),
        ("20261024", "3.3429"),
        ("20261025", "3.3429"),
    ),
    "FR10Y-FR": _payload(("20261022", "5.0890"), ("20261023", "5.0192"), ("20261024", "5.0192")),
    "IT10Y-IT": _payload(("20261022", "4.6121")),
    "ES10Y-ES": _payload(("20261022", "4.0595"), ("20261023", "4.0432")),
}


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FakeSession:
    headers: dict = {}

    def get(self, url, params=None, timeout=None):
        return _Resp(BARS[params["symbol"]])


def test_parses_four_countries(monkeypatch):
    monkeypatch.setattr(
        "src.fetchers.euro_sovereign_fetcher.requests.Session", lambda: _FakeSession()
    )
    df = fetch_euro_yields()
    assert list(df.columns) == ["DE", "FR", "IT", "ES"]
    assert df.index[-1] == pd.Timestamp("2026-10-23")
    assert df.iloc[-1]["FR"] == 5.0192


def test_drops_weekend_rows(monkeypatch):
    """周六日那两行是周五拷贝，不能当新观测入库。"""
    monkeypatch.setattr(
        "src.fetchers.euro_sovereign_fetcher.requests.Session", lambda: _FakeSession()
    )
    df = fetch_euro_yields()
    assert (df.index.dayofweek >= 5).sum() == 0
    assert len(df) == 2  # 只剩 10-22 / 10-23


def test_missing_country_day_leaves_nan(monkeypatch):
    """某国缺一天 → NaN（不 ffill 造假），日期轴仍以整张表为准。"""
    monkeypatch.setattr(
        "src.fetchers.euro_sovereign_fetcher.requests.Session", lambda: _FakeSession()
    )
    df = fetch_euro_yields()
    assert pd.isna(df.loc["2026-10-23", "IT"])
    assert df["IT"].isna().sum() == 1
