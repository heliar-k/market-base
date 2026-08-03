"""Barchart 期权链 fetcher 单元测试。"""

from src.fetchers.barchart_client import to_float
from src.fetchers.barchart_options_fetcher import (
    _exp_to_yyyymmdd,
    fetch_barchart_chain,
)


def test_to_float_cleans_values():
    """千分位、百分比、N/A 清洗。"""
    assert to_float("5,966") == 5966.0
    assert to_float("28.39%") == 0.2839
    assert to_float("N/A") is None
    assert to_float(None) is None


def test_exp_to_yyyymmdd():
    assert _exp_to_yyyymmdd("08/21/26") == "20260821"
    assert _exp_to_yyyymmdd("2026-08-21") == "20260821"
    assert _exp_to_yyyymmdd("") == ""


def test_fetch_barchart_chain(monkeypatch):
    """链解析：call/put 符号翻转、iv 小数化、OI/volume 填 0。"""
    payload = {
        "data": [
            {
                "strikePrice": "300.00",
                "volume": "5,966",
                "openInterest": "14,729",
                "volatility": "28.39%",
                "gamma": "0.0171",
                "expirationDate": "08/21/26",
                "optionType": "Call",
            },
            {
                "strikePrice": "300.00",
                "volume": "N/A",
                "openInterest": "N/A",
                "volatility": "28.50%",
                "gamma": "0.0172",
                "expirationDate": "08/21/26",
                "optionType": "Put",
            },
            {"strikePrice": "N/A", "gamma": "N/A", "optionType": "N/A"},  # 无效行
        ]
    }

    monkeypatch.setattr(
        "src.fetchers.barchart_options_fetcher._chain_get",
        lambda symbol, params: payload,
    )

    df = fetch_barchart_chain("AAPL", ["20260821"])
    assert len(df) == 2
    row = df.set_index("right")
    assert row.loc["C", "gamma"] == 0.0171  # call 正
    assert row.loc["P", "gamma"] == -0.0172  # put 翻转
    assert row.loc["C", "iv"] == 0.2839  # 百分比 → 小数
    assert row.loc["C", "openInterest"] == 14729
    assert row.loc["P", "openInterest"] == 0  # N/A → 0
    assert row.loc["C", "expiration"] == "20260821"
