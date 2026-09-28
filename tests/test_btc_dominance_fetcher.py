"""CoinGecko BTC/ETH Dominance fetcher 单元测试（mock requests，不联网）。"""

import sys

import pandas as pd
import pytest

from src.fetchers import btc_dominance_fetcher as bd


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _payload(btc: float, eth: float) -> dict:
    return {"data": {"market_cap_percentage": {"btc": btc, "eth": eth, "usdt": 5.0}}}


def test_fetch_dominance_parses(monkeypatch):
    monkeypatch.setattr(
        bd.requests, "get", lambda *a, **k: _Resp(_payload(58.1234, 11.987))
    )
    df = bd.fetch_dominance()
    assert list(df.columns) == ["btc_dominance", "eth_dominance"]
    row = df.iloc[0]
    assert row["btc_dominance"] == 58.12  # 保留 2 位小数
    assert row["eth_dominance"] == 11.99
    pd.Timestamp(df.index[0])  # index 是 ISO 日期字符串


def test_main_upsert_roundtrip(monkeypatch, tmp_path):
    out = tmp_path / "btc_dominance.csv"
    monkeypatch.setattr(bd, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["fetch_btc_dominance"])
    monkeypatch.setattr(
        bd.requests, "get", lambda *a, **k: _Resp(_payload(58.12, 11.99))
    )
    bd.main()
    df = pd.read_csv(out)
    assert list(df.columns) == ["date", "btc_dominance", "eth_dominance"]
    assert len(df) == 1
    assert df.loc[0, "btc_dominance"] == 58.12
    # 同日重跑覆盖不追加
    bd.main()
    assert len(pd.read_csv(out)) == 1


def test_missing_keys_raise(monkeypatch):
    monkeypatch.setattr(bd.requests, "get", lambda *a, **k: _Resp({"data": {}}))
    with pytest.raises(KeyError):
        bd.fetch_dominance()
