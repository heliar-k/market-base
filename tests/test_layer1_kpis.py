"""LAYER 1 · NOW 8-KPI 卡分析层单元测试。

tmp_path 隔离（monkeypatch assets_analysis.ROOT）+ monkeypatch 网络函数，
不真实联网。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import src.assets_analysis as aa

_LABELS = [
    "BTC 现价",
    "BTC 30d 已实现波动率",
    "基差 60d EMA",
    "Spread（基差−SOFR）",
    "永续资金费率 8h",
    "CME BTC OI",
    "永续多空比",
    "期权 Put/Call OI",
]


def _write(root, path: str, text: str) -> None:
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _snapshot(last: float = 100.0, pcr: float = 0.8, fut_oi: int = 1000) -> dict:
    return {
        "ts": "2026-08-23T00:00:00Z",
        "title": "加密衍生品快照",
        "perp": {
            "BTC": {
                "funding_rate": 0.0001,
                "funding_annual": 0.1095,
                "oi_usd": 1e9,
                "oi_ccy": 1000.0,
                "last": last,
                "ts": 1,
            }
        },
        "taker": {
            "BTC": [
                {"date": "2026-08-22", "buy": 100.0, "sell": 80.0},
                {"date": "2026-08-21", "buy": 90.0, "sell": 100.0},
                {"date": "2026-08-20", "buy": 110.0, "sell": 90.0},
            ]
        },
        "options_BTC": {
            "spot_anchor": last,
            "pcr": pcr,
            "call_wall": 110.0,
            "put_wall": 90.0,
        },
        "cme": {"fut_price": 101.0, "spot": 100.0, "basis_pct": 1.0, "fut_oi": fut_oi},
    }


def _mk(
    tmp_path,
    prices: list[float] | None = None,
    snap_last: float | None = None,
    pcr: float = 0.8,
) -> None:
    """最小 fixture：BTC 日线 + 基差 + SOFR + COT + 2 个快照 json。"""
    root = tmp_path
    prices = prices or [float(i) for i in range(1, 101)]
    n = len(prices)
    d = pd.bdate_range("2024-01-01", periods=n)
    _write(
        root,
        "data/yfinance/asset_prices.csv",
        "date,BTC\n" + "".join(f"{dd.date()},{p}\n" for dd, p in zip(d, prices)),
    )
    _write(
        root,
        "data/crypto_basis/basis.csv",
        "date,basis_pct,fut_close,spot_close\n"
        + "".join(f"{dd.date()},{p / 10},100,95\n" for dd, p in zip(d, prices)),
    )
    _write(
        root,
        "data/fred/rates/rates.csv",
        "date,SOFR\n2026-08-01,3.5\n2026-08-02,3.6\n",
    )
    w = pd.date_range("2025-01-06", periods=n, freq="W-FRI")
    _write(
        root,
        "data/cot/cot.csv",
        "date,BTC_OI\n"
        + "".join(
            f"{dd.date()},{int(1000 + 1000 * p / 100)}\n" for dd, p in zip(w, prices)
        ),
    )
    last = snap_last if snap_last is not None else prices[-1]
    for i in range(2):
        _write(
            root,
            f"data/crypto_derivatives/2026080{i + 1}.json",
            json.dumps(_snapshot(last=last, pcr=pcr)),
        )


def _mock_net(monkeypatch) -> None:
    """mock 网络拉取函数（funding ≥30 条、taker 10 条）。"""
    monkeypatch.setattr(
        aa, "_okx_funding_history", lambda: [0.0001 * i for i in range(1, 41)]
    )
    monkeypatch.setattr(
        aa, "_okx_taker_history", lambda: [1.0 + 0.01 * i for i in range(10)]
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """完整数据环境（ROOT→tmp_path + 网络 mock）。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)
    _mock_net(monkeypatch)
    return tmp_path


def _by_label(d: dict) -> dict:
    return {k["label"]: k for k in d["kpis"]}


def test_layer1_eight_kpis_and_fields(setup):
    d = aa._layer1_kpis()
    kpis = d["kpis"]
    assert [k["label"] for k in kpis] == _LABELS
    for k in kpis:
        fields = {"label", "value", "pct_rank", "pct_label", "chg", "quartiles", "note"}
        assert fields <= set(k)


def test_pct_rank_known_series(setup):
    """BTC 价 1..100、快照 last=100 → 百分位 100.0；P50=50.5；chg=↑+1.01%。"""
    by = _by_label(aa._layer1_kpis())
    k = by["BTC 现价"]
    assert k["pct_rank"] == 100.0
    assert k["value"] == "$100"
    assert k["chg"] == "↑ +1.01%"
    q = k["quartiles"]
    assert q is not None and len(q) == 3
    assert abs(q[1] - 50.5) < 0.01
    assert k["pct_label"] == "过去 100 天 · 第 100 百分位"


def test_pct_rank_mid_series(tmp_path, monkeypatch):
    """BTC 价 1..100、快照 last=50 → 百分位 50.0。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path, snap_last=50.0)
    _mock_net(monkeypatch)
    k = _by_label(aa._layer1_kpis())["BTC 现价"]
    assert k["pct_rank"] == 50.0
    assert k["value"] == "$50"


def test_insufficient_data_pct_none(tmp_path, monkeypatch):
    """10 天数据 + 5 条 funding + 3 条 taker + 2 个快照。

    全部 pct_rank=None 且带 note。
    """
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path, prices=[float(i) for i in range(1, 11)])
    monkeypatch.setattr(aa, "_okx_funding_history", lambda: [0.0001] * 5)
    monkeypatch.setattr(aa, "_okx_taker_history", lambda: [1.0] * 3)
    d = aa._layer1_kpis()
    for k in d["kpis"]:
        assert k["pct_rank"] is None, k["label"]
        assert k["pct_label"] == "历史不足", k["label"]
        assert k["note"], k["label"]
    by = _by_label(d)
    assert "快照积累中" in by["期权 Put/Call OI"]["note"]
    assert "不足" in by["BTC 现价"]["note"]


def test_network_failure_degrades(tmp_path, monkeypatch):
    """OKX 网络异常（fetcher._okx 抛错）→ 拉取函数返回 []、KPI 不抛异常。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("src.fetchers.crypto_derivatives_fetcher._okx", boom)
    assert aa._okx_funding_history() == []
    assert aa._okx_taker_history() == []

    d = aa._layer1_kpis()  # funding/taker 历史为空 → 降级，仍返回 8 个 KPI
    assert len(d["kpis"]) == 8
    by = _by_label(d)
    assert by["永续资金费率 8h"]["pct_rank"] is None
    # 快照仍有当前值（funding/taker 来自 json，不依赖网络）
    assert by["永续资金费率 8h"]["value"] == "0.0100%"
    assert by["永续多空比"]["value"] == "1.11"
    assert by["永续多空比"]["pct_rank"] is None


def test_funding_kpi_uses_mocked_history(setup, monkeypatch):
    """funding 历史被 mock → 百分位用当前值在历史里的位置。"""
    monkeypatch.setattr(
        aa, "_okx_funding_history", lambda: [0.006 * i for i in range(1, 41)]
    )
    by = _by_label(aa._layer1_kpis())
    k = by["永续资金费率 8h"]
    # 当前 0.01%（0.0001×100），历史 0.006%..0.24% 共 40 条 → 仅 1 条 ≤ 0.01 → rank 2.5
    assert k["pct_rank"] == 2.5
    assert k["quartiles"] is not None


def test_crypto_derivatives_wires_layer1(setup):
    d = aa.crypto_derivatives()
    assert d is not None
    assert "layer1" in d
    assert len(d["layer1"]["kpis"]) == 8
