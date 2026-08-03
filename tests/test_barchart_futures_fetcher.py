"""Barchart 期货期限结构 fetcher 单元测试。"""

from src.fetchers.barchart_client import to_float
from src.fetchers.barchart_futures_fetcher import fetch_futures_curves


def test_to_float_cleans_barchart_numbers():
    """'9,108.25s' → 9108.25；N/A → None；空 → None。"""
    assert to_float("9,108.25s") == 9108.25
    assert to_float("-1,039.9600") == -1039.96
    assert to_float("95.1050s") == 95.105
    assert to_float("N/A") is None
    assert to_float(None) is None


def test_fetch_futures_curves(monkeypatch):
    """多品种拉取：单品种失败不阻塞，列名用 Barchart 合约代码。"""
    recs_es = [
        {
            "symbol": "ESU31",
            "contractExpirationDate": "09/19/31",
            "lastPrice": "9,108.25s",
        },
        {"symbol": "ESM31", "contractExpirationDate": "06/20/31", "lastPrice": "N/A"},
    ]

    def fake_core_get(params, referer):
        if "ES" in params["symbol"]:
            return {"data": recs_es}
        raise RuntimeError("boom")  # GC 失败

    monkeypatch.setattr("src.fetchers.barchart_futures_fetcher.core_get", fake_core_get)

    df = fetch_futures_curves(["ES", "GC"])
    assert df.shape == (1, 1)
    assert df.iloc[0]["ESU31"] == 9108.25  # N/A 合约被跳过

    assert fetch_futures_curves(["GC"]).empty  # 全部失败 → 空
