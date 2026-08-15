"""Barchart 波动率快照 fetcher 单元测试（$ 剥离 / % 转百分数 / 宽表）。"""

from src.fetchers.barchart_vol_fetcher import fetch_volatility_snapshot


def test_fetch_volatility_snapshot(monkeypatch):
    """30 指数快照 → 单行宽表；chg 列 = Barchart 百分比 ×100。"""
    recs = [
        {
            "symbol": "$VIX",
            "lastPrice": "14.25",
            "percentChange": "-2.60%",
            "percentChange5d": "-4.36%",
            "percentChange1m": "-13.64%",
            "percentChange1y": "-3.91%",
        },
        {
            "symbol": "$VXMO",
            "lastPrice": "14.70",
            "percentChange": "-2.65%",
            "percentChange5d": "N/A",
            "percentChange1m": "N/A",
            "percentChange1y": "N/A",
        },
        {
            "symbol": "$MOVE",
            "lastPrice": "N/A",
            "percentChange": "+0.51%",
            "percentChange5d": "N/A",
            "percentChange1m": "N/A",
            "percentChange1y": "N/A",
        },
    ]

    def fake_core_get(params, referer):
        assert params["list"] == "stocks.markets.volatility"
        return {"data": recs}

    monkeypatch.setattr("src.fetchers.barchart_vol_fetcher.core_get", fake_core_get)
    df = fetch_volatility_snapshot()

    assert len(df) == 1
    assert df.loc[df.index[0], "VIX"] == 14.25
    assert df.loc[df.index[0], "VIX_chg1d"] == -2.6  # '-2.60%' → -2.60 百分数
    assert df.loc[df.index[0], "VIX_chg5d"] == -4.36
    assert df.loc[df.index[0], "VXMO_chg1d"] == -2.65
    assert "VXMO_chg5d" not in df.columns  # N/A 的 chg 列不创建
    assert "MOVE" not in df.columns  # lastPrice N/A 的行不产生价格列
    assert df.loc[df.index[0], "MOVE_chg1d"] == 0.51  # 价格缺失但 chg 仍在


def test_fetch_empty_returns_empty(monkeypatch):
    def fake_core_get(params, referer):
        return {"data": []}

    monkeypatch.setattr("src.fetchers.barchart_vol_fetcher.core_get", fake_core_get)
    assert fetch_volatility_snapshot().empty
