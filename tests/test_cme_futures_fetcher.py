"""CME BTC 期货仓位 fetcher 测试（样本内嵌，不联网）。"""

import json

import pytest

from src.fetchers import cme_futures_fetcher as mod
from src.fetchers.cme_futures_fetcher import (
    _expiry,
    _fetch_trade_date,
    _to_float,
    build_snapshot,
    fetch_snapshot,
    snapshot_plausible,
)

# 真实响应形态（2026-09-25，数值简化）
SAMPLE = {
    "settlements": [
        {
            "month": "SEP 26",
            "settle": "83671.72",
            "volume": "688",
            "openInterest": "3,584",
        },
        {
            "month": "OCT 26",
            "settle": "84420.00",
            "volume": "8,674",
            "openInterest": "17,676",
        },
        {
            "month": "NOV 26",
            "settle": "84800.00",
            "volume": "351",
            "openInterest": "603",
        },
        {
            "month": "DEC 26",
            "settle": "85175.00A",
            "volume": "20",
            "openInterest": "439",
        },
    ]
}


@pytest.fixture(autouse=True)
def _spot(monkeypatch):
    """锁死 BTC 现价锚 ~$83,000（不依赖真实 asset_prices.csv）。"""
    monkeypatch.setattr(mod, "_btc_spot", lambda: 83000.0)


def test_to_float():
    assert _to_float("83,671.72") == 83671.72
    assert _to_float("85175.00A") == 85175.0  # ask 后缀
    assert _to_float("85175.00B") == 85175.0  # bid 后缀
    assert _to_float("-") is None
    assert _to_float(None) is None


def test_expiry_last_friday():
    assert _expiry("SEP 26") == "2026-09-25"  # 2026-09 最后一个周五
    assert _expiry("OCT 26") == "2026-10-30"
    assert _expiry("DEC 26") == "2026-12-25"
    assert _expiry("bad") is None


def test_fetch_trade_date_parses_and_sorts(monkeypatch):
    monkeypatch.setattr(mod, "_get", lambda url: SAMPLE)
    from datetime import datetime

    rows = _fetch_trade_date(datetime(2026, 9, 25))
    assert [r["month"] for r in rows] == ["SEP 26", "OCT 26", "NOV 26", "DEC 26"]
    assert rows[1]["oi"] == 17676
    assert rows[1]["volume"] == 8674
    assert rows[0]["expiry"] == "2026-09-25"


def test_build_snapshot_fields():
    from datetime import datetime

    rows = _fetchable_rows()
    snap = build_snapshot(rows, datetime(2026, 9, 25))
    assert snap["total_oi"] == 22302
    assert snap["total_oi_btc"] == 22302 * 5
    assert snap["front"]["month"] == "SEP 26"
    assert snap["next"]["month"] == "OCT 26"
    # 主力→次主力年化：(84420/83671.72 − 1) × 365/35 ≈ +9.33% → contango
    assert snap["basis_ann_pct"] == pytest.approx(9.33, abs=0.05)
    assert snap["term"] == "contango"
    assert snap["as_of"] == "2026-09-25"


def _fetchable_rows():
    return [
        {
            "month": "SEP 26",
            "expiry": "2026-09-25",
            "settle": 83671.72,
            "volume": 688,
            "oi": 3584,
        },
        {
            "month": "OCT 26",
            "expiry": "2026-10-30",
            "settle": 84420.0,
            "volume": 8674,
            "oi": 17676,
        },
        {
            "month": "NOV 26",
            "expiry": "2026-11-27",
            "settle": 84800.0,
            "volume": 351,
            "oi": 603,
        },
        {
            "month": "DEC 26",
            "expiry": "2026-12-25",
            "settle": 85175.0,
            "volume": 20,
            "oi": 439,
        },
    ]


def test_guard_rejects_garbage():
    snap = build_snapshot(
        _fetchable_rows(), __import__("datetime").datetime(2026, 9, 25)
    )
    assert snapshot_plausible(snap, 83000.0) is True
    assert snapshot_plausible(snap, None) is False  # 无锚 → 拒
    zero = {**snap, "total_oi": 0}
    assert snapshot_plausible(zero, 83000.0) is False
    wrong_scale = json.loads(json.dumps(snap))
    wrong_scale["front"]["settle"] = 837.0  # 错量级
    assert snapshot_plausible(wrong_scale, 83000.0) is False


def test_fetch_snapshot_walkback(monkeypatch):
    """当天/前一天无数据（周末）→ 回退到周五；全部失败 → {}。"""

    calls = []

    def fake_get(url):
        # tradeDate 在 URL 里：09/28/2026（周一例）无数据，09/25 有
        calls.append(url)
        if "09/25/2026" in url:
            return SAMPLE
        return {"settlements": []}

    monkeypatch.setattr(mod, "_get", fake_get)
    snap = fetch_snapshot()
    assert snap["as_of"] == "2026-09-25"
    assert len(calls) <= 7
    # 全空 → {}
    monkeypatch.setattr(mod, "_get", lambda url: {"settlements": []})
    assert fetch_snapshot() == {}


def test_crypto_derivatives_wires_cme_futures(monkeypatch, tmp_path):
    """crypto_derivatives 组装含 cme_futures（校验不过的垃圾快照 → unavailable）。"""
    from src import assets_analysis as aa

    monkeypatch.setattr(aa, "ROOT", tmp_path)
    d = aa.crypto_derivatives()
    assert d is None or d.get("cme_futures", {}).get("available", False) is False
    # 好快照 → 透传 + OI 日变化
    out = tmp_path / "data" / "crypto_derivatives"
    out.mkdir(parents=True, exist_ok=True)
    (out / "20260925.json").write_text(
        json.dumps({"ts": "x", "perp": {}, "options_BTC": {}, "taker": {}}),
        encoding="utf-8",
    )
    cf = tmp_path / "data" / "cme_futures"
    cf.mkdir(parents=True)
    snap = build_snapshot(
        _fetchable_rows(), __import__("datetime").datetime(2026, 9, 25)
    )
    (cf / "20260925.json").write_text(json.dumps(snap), encoding="utf-8")
    snap2 = dict(snap, total_oi=snap["total_oi"] + 100)
    (cf / "20260928.json").write_text(json.dumps(snap2), encoding="utf-8")
    d = aa.crypto_derivatives()
    assert d["cme_futures"]["available"] is True
    assert d["cme_futures"]["total_oi"] == snap2["total_oi"]
    assert d["cme_futures"]["oi_chg_1d"] == 100
