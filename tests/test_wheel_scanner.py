"""Tests for src.wheel_scanner."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.wheel_scanner import WHEEL_POOL, PoolSpec, _pick_contract, evaluate_symbol


def test_wheel_pool_has_tiers_and_unique_symbols() -> None:
    syms = [p.symbol for p in WHEEL_POOL]
    assert len(syms) == len(set(syms))
    assert {"T0", "T1", "T2"} <= {p.tier for p in WHEEL_POOL}
    assert {"V", "MA", "JPM", "NVDA", "GOOG", "UNH", "TSM"} <= set(syms)


def test_evaluate_symbol_sell_put_signal(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    stocks_dir = tmp_path / "data" / "stocks"
    stocks_dir.mkdir(parents=True)

    # 构造 220 天先涨后急跌的序列，触发 RSI<=40 且跌破 MA50
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

    spec = PoolSpec("TEST", "T0", "测试", "测试护城河")
    opt_map = {
        "TEST": {
            "iv_front": {"dte": 7, "iv": 32.0},
            "iv_back": {"dte": 35, "iv": 38.0},
            "put_wall": {"strike": 230.0},
            "call_wall": {"strike": 280.0},
        }
    }
    row = evaluate_symbol(spec, "20261023", opt_map)
    assert row is not None
    assert row.suitability == "黄金适格"
    assert row.signal == "🟢 SELL PUT"
    assert row.put_pick is not None
    assert row.put_pick.right == "P"
    assert row.put_pick.strike < row.close


def test_bsm_fallback_pick() -> None:
    ov = {"iv_back": {"dte": 35, "iv": 35.0}}
    pick = _pick_contract("FAKE", None, ov, 300.0, "P", -0.22)
    assert pick is not None
    assert pick.strike < 300.0
    assert -0.34 <= pick.delta <= -0.10


def test_earnings_danger_blocks_sell_put(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta

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
            "iv_front": {"dte": 7, "iv": 45.0},
            "iv_back": {"dte": 35, "iv": 38.0},
            "earnings_date": soon,
        }
    }
    row = evaluate_symbol(spec, "20261023", opt_map)
    assert row is not None
    assert row.signal == "⛔ 财报等落地"
    assert "⚠️财报4d" in row.event_alert
