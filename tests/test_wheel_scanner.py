"""Tests for src.wheel_scanner."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.wheel_scanner import WHEEL_POOL, PoolSpec, _iv_at_dte, _pick_contract, evaluate_symbol


def test_wheel_pool_has_tiers_and_unique_symbols() -> None:
    syms = [p.symbol for p in WHEEL_POOL]
    assert len(syms) == len(set(syms))
    assert {"T0", "T1", "T2"} <= {p.tier for p in WHEEL_POOL}
    assert {"V", "MA", "JPM", "NVDA", "GOOG", "UNH", "TSM"} <= set(syms)


def test_iv_at_dte_interpolates_35d_instead_of_leaps() -> None:
    ov = {
        "iv_curve": [
            {"dte": 7, "iv": 30.0},
            {"dte": 35, "iv": 36.0},
            {"dte": 328, "iv": 48.0},
        ],
        "iv_back": {"dte": 328, "iv": 48.0},
    }
    assert _iv_at_dte(ov, 35) == 36.0


def test_evaluate_symbol_sell_put_requires_stabilization_and_vrp(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    stocks_dir = tmp_path / "data" / "stocks"
    stocks_dir.mkdir(parents=True)

    # 构造 220 天序列：前 200 天上涨，后 19 天回调，最后 1 天小幅企稳反弹（收复前高/MACD拐头）
    dates = pd.date_range("2026-01-01", periods=220, freq="B")
    prices = np.concatenate([np.linspace(200, 300, 200), np.linspace(295, 270, 19), [273.0]])
    df = pd.DataFrame(
        {
            "open": prices * 0.995,
            "high": prices * 1.005,
            "low": prices * 0.99,
            "close": prices,
            "volume": 1_000_000,
        },
        index=dates,
    )
    df.index.name = "date"
    df.to_csv(stocks_dir / "TEST.csv")

    spec = PoolSpec("TEST", "T0", "AI算力", "测试护城河")
    opt_map = {
        "TEST": {
            "iv_curve": [{"dte": 14, "iv": 34.0}, {"dte": 35, "iv": 38.0}],
            "iv_rank_1y": 55.0,
            "iv_pct_1y": 70.0,
            "put_wall": {"strike": 250.0},
            "call_wall": {"strike": 310.0},
            "earnings_date": (datetime.now().date() + timedelta(days=60)).strftime("%Y-%m-%d"),
        }
    }
    row = evaluate_symbol(spec, "20261023", opt_map)
    assert row is not None
    assert row.suitability == "黄金适格"
    assert row.signal == "🟢 SELL PUT"
    assert row.put_pick is not None
    assert row.put_pick.right == "P"
    assert row.put_pick.strike < row.close
    assert row.put_pick.executable is False  # 无 raw CSV 时显式标为理论估算


def test_negative_vrp_blocks_sell_put(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    stocks_dir = tmp_path / "data" / "stocks"
    stocks_dir.mkdir(parents=True)

    dates = pd.date_range("2026-01-01", periods=220, freq="B")
    rng = np.random.default_rng(42)
    base = np.concatenate([np.linspace(200, 300, 200), np.linspace(295, 245, 20)])
    prices = base * (1.0 + rng.normal(0, 0.015, 220))
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": 1_000_000,
        },
        index=dates,
    )
    df.index.name = "date"
    df.to_csv(stocks_dir / "TEST.csv")

    spec = PoolSpec("TEST", "T0", "测试", "测试")
    # 故意给低于 HV20(~25%) 的 35d IV(24.5%)，验证 VRP<2% 被拦截且不输出 put_pick
    opt_map = {
        "TEST": {
            "iv_curve": [{"dte": 14, "iv": 24.0}, {"dte": 35, "iv": 24.5}],
            "iv_rank_1y": 50.0,
            "iv_pct_1y": 50.0,
            "earnings_date": (datetime.now().date() + timedelta(days=60)).strftime("%Y-%m-%d"),
        }
    }
    row = evaluate_symbol(spec, "20261023", opt_map)
    assert row is not None
    assert row.signal == "⚪ 权利金偏薄"
    assert row.put_pick is None


def test_bsm_fallback_pick() -> None:
    ov = {"iv_curve": [{"dte": 14, "iv": 32.0}, {"dte": 35, "iv": 35.0}]}
    pick = _pick_contract("FAKE", None, ov, 300.0, 35.0, "P", -0.22)
    assert pick is not None
    assert pick.strike < 300.0
    assert pick.executable is False
    assert -0.32 <= pick.delta <= -0.12


def test_earnings_danger_blocks_sell_put_and_omits_contract(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    stocks_dir = tmp_path / "data" / "stocks"
    stocks_dir.mkdir(parents=True)

    dates = pd.date_range("2026-01-01", periods=220, freq="B")
    prices = np.concatenate([np.linspace(200, 300, 200), np.linspace(295, 240, 20)])
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": 1_000_000,
        },
        index=dates,
    )
    df.index.name = "date"
    df.to_csv(stocks_dir / "TEST.csv")

    soon = (datetime.now().date() + timedelta(days=4)).strftime("%Y-%m-%d")
    spec = PoolSpec("TEST", "T0", "测试", "测试护城河")
    opt_map = {
        "TEST": {
            "iv_curve": [{"dte": 7, "iv": 45.0}, {"dte": 35, "iv": 38.0}],
            "iv_rank_1y": 75.0,
            "iv_pct_1y": 85.0,
            "earnings_date": soon,
        }
    }
    row = evaluate_symbol(spec, "20261023", opt_map)
    assert row is not None
    assert row.signal == "⛔ 财报等落地"
    assert "⚠️财报4d" in row.event_alert
    assert row.put_pick is None  # 否决态绝不挂推荐合约
