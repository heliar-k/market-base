"""期权结构快照聚合逻辑测试（Gamma Flip 最近穿越选择）。

回归背景：旧逻辑只找现价下方第一个累计 GEX 负转正穿越点，①翻转点在现价
上方时漏检（REGIME 卡回退 net_gex 符号后与分布图矛盾）②深 ITM 噪声穿越
被误当翻转点。新逻辑取离现价最近的穿越点（上下皆可），保证
sign(flip_dist) == sign(现价处累计 GEX)。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from src.options_structure import compute_structure

EXP = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")


def _chain(legs: list[tuple[float, str, int]]) -> pd.DataFrame:
    """legs: (strike, right, oi) → 单到期日合成期权链（IV 0.2）。"""
    return pd.DataFrame(
        [
            {
                "strike": strike,
                "right": right,
                "expiration": EXP,
                "openInterest": oi,
                "volume": 0,
                "impliedVolatility": 0.2,
            }
            for strike, right, oi in legs
        ]
    )


def test_flip_nearest_crossing_above_spot():
    """现价处累计为负、穿越点在现价上方 → flip 取上方穿越（旧逻辑漏检返回 None）。"""
    out = compute_structure(
        _chain(
            [
                (90.0, "P", 50000),
                (95.0, "P", 50000),
                (100.0, "P", 20000),
                (105.0, "C", 90000),
                (110.0, "C", 10000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 105.0
    assert out["flip_dist"] < 0  # 现价在 Flip 下方 → 局部负 Gamma


def test_flip_nearest_of_multiple():
    """多个穿越点取离现价最近的（旧逻辑取第一个，被深 ITM 噪声带偏）。"""
    out = compute_structure(
        _chain(
            [
                (80.0, "P", 80000),  # 深负
                (84.0, "C", 60000),  # 第一次上穿（噪声位，旧逻辑取这里）
                (92.0, "P", 50000),  # 下穿
                (96.0, "C", 50000),  # 上穿 —— 离现价最近
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 96.0
    assert out["flip_dist"] > 0  # 现价在 Flip 上方 → 局部正 Gamma


def test_flip_none_when_no_crossing():
    """全链累计恒为正（无穿越）→ flip/flip_dist 均为 None。"""
    out = compute_structure(
        _chain(
            [
                (95.0, "C", 20000),
                (100.0, "C", 50000),
                (105.0, "C", 20000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] is None
    assert out["flip_dist"] is None


def test_flip_ignores_deep_itm_junk_outside_band():
    """回归：高价新标的链含 $5 级深 ITM 存量（如 MU 20260911 flip=105 @ spot=977）。
    带外（现价 ±50%~200% 之外）的噪声穿越不得成为 flip。"""
    out = compute_structure(
        _chain(
            [
                (10.0, "P", 200000),  # 带外深 ITM：巨量 put 拉负
                (20.0, "C", 200000),  # 带外噪声穿越（旧逻辑会返回 20）
                (60.0, "C", 30000),  # 带内累计回正，此后无负→正穿越
                (100.0, "C", 30000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] is None


def test_dte_uses_et_calendar_date(monkeypatch):
    import src.options_structure as os

    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 29, tzinfo=ZoneInfo("America/New_York")),
    )
    rows = [
        {
            "strike": 100.0,
            "right": "C",
            "expiration": exp,
            "openInterest": 10,
            "impliedVolatility": 0.2,
            "volume": 0,
        }
        for exp in ("20261027", "20261028", "20261029")
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert [p["dte"] for p in out["iv_curve"]] == [0, 1, 2]
    assert out["buckets"]["0DTE"]["contracts"] == 10


def test_atm_iv_averages_call_and_put_at_same_strike():
    rows = [
        {
            "strike": 100.0,
            "right": right,
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": iv,
            "volume": 0,
        }
        for right, iv in (("C", 0.2), ("P", 0.4))
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert out["iv_front"]["iv"] == 30.0


def test_skew_uses_closest_25_delta_call_and_put():
    rows = [
        {
            "strike": 94.0,
            "right": "P",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.4,
            "volume": 0,
        },
        {
            "strike": 93.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.6,
            "volume": 0,
        },
        {
            "strike": 105.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.25,
            "volume": 0,
        },
        {
            "strike": 105.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.27,
            "volume": 0,
        },
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert out["skew_put_iv"] == 40.0
    assert out["skew_call_iv"] == 25.0
    assert out["skew"] == 15.0


def test_fetch_chain_rejects_premarket_garbage_without_caching(monkeypatch, tmp_path):
    import yfinance as yf

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    expiration = "20261120"
    raw = tmp_path / f"raw_NVDA_{os._now_et():%Y%m%d}.csv"
    bad = pd.DataFrame(
        {
            "strike": [200.0],
            "bid": [0.0],
            "ask": [0.01],
            "openInterest": [0],
            "volume": [0],
            "impliedVolatility": [0.001],
        }
    )
    cached = bad.copy()
    cached["right"] = "P"
    cached["expiration"] = expiration
    cached.to_csv(raw, index=False)
    chain = type("Chain", (), {"calls": bad, "puts": bad.copy()})()
    ticker = type("Ticker", (), {"option_chain": lambda self, exp: chain})()
    monkeypatch.setattr(yf, "Ticker", lambda symbol: ticker)

    out = os.fetch_chain("NVDA", [expiration])
    assert out.empty
    assert not raw.exists()


def test_main_merges_partial_full_run_with_existing_snapshot(monkeypatch, tmp_path):
    import json
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 29, tzinfo=ZoneInfo("America/New_York")),
    )
    path = tmp_path / "20261027.json"
    path.write_text(
        json.dumps({"date": "old", "symbols": {"MSFT": {"symbol": "MSFT"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-27",
            "symbols": {"NVDA": {"symbol": "NVDA"}},
        },
    )
    monkeypatch.setattr(sys, "argv", ["options_structure"])

    os.main()
    out = json.loads(path.read_text(encoding="utf-8"))
    assert set(out["symbols"]) == {"MSFT", "NVDA"}
    assert out["date"] == "new"
    assert out["snapshot_date"] == "2026-10-27"


def test_wall_exports_full_cumulative_gex():
    out = compute_structure(
        _chain(
            [
                (80.0, "P", 80000),
                (84.0, "C", 60000),
                (92.0, "P", 50000),
                (96.0, "C", 50000),
            ]
        ),
        100.0,
    )
    prev = 0.0
    crossings = []
    for row in out["wall"]:
        cum = row["cum_gex_m"]
        if prev < 0 <= cum:
            crossings.append(row["strike"])
        prev = cum
    assert 96.0 in crossings


def test_oi_top_uses_total_open_interest():
    rows = [
        {
            "strike": float(strike),
            "right": "C",
            "expiration": EXP,
            "openInterest": 2000 - i,
            "volume": 0,
            "impliedVolatility": 0.2,
        }
        for i, strike in enumerate(range(80, 101))
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert len(out["oi_top"]) == 20
    assert out["oi_top"][0]["strike"] == 80.0
    assert 100.0 not in {row["strike"] for row in out["oi_top"]}


def test_raw_retention_keeps_latest_three_actual_dates(monkeypatch, tmp_path):
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-26",
            "symbols": {"NVDA": {"symbol": "NVDA"}},
        },
    )
    for stamp in ("20261021", "20261022", "20261023", "20261026", "20261027"):
        (tmp_path / f"raw_NVDA_{stamp}.csv").write_text("strike\n100\n")
    monkeypatch.setattr(sys, "argv", ["options_structure"])

    os.main()

    assert (tmp_path / "raw_NVDA_20261022.csv").exists()
    assert (tmp_path / "raw_NVDA_20261023.csv").exists()
    assert (tmp_path / "raw_NVDA_20261026.csv").exists()
    assert not (tmp_path / "raw_NVDA_20261021.csv").exists()
    assert not (tmp_path / "raw_NVDA_20261027.csv").exists()
