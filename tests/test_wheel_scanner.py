"""Tests for src.wheel_scanner."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.wheel_scanner import (
    WHEEL_POOL,
    PoolSpec,
    _directional_walls,
    _event_fields,
    _iv_at_dte,
    _pick_contract,
    evaluate_symbol,
)


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
    assert row.suitability == "适格"
    assert row.signal == "SELL PUT"
    assert row.signal_tone == "bull"
    assert row.put_pick is not None
    assert row.put_pick.right == "P"
    assert row.put_pick.strike < row.close
    assert row.put_pick.executable is False  # 无 raw CSV 时显式标为理论估算
    assert row.opt_symbol == "TEST"  # 未指定别名时 = symbol

    # GOOG/BRK.B 类：池内 symbol 与期权链 ticker 不一致时走别名（前端点击联动用）
    aliased = evaluate_symbol(
        PoolSpec("TEST", "T0", "AI算力", "测试护城河", "TESTX"), "20261023", opt_map
    )
    assert aliased is not None
    assert (aliased.symbol, aliased.opt_symbol) == ("TEST", "TESTX")


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
    assert row.signal == "暂不操作"
    assert "权利金偏薄" in row.reason
    assert row.signal_tone == "flat"
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

    soon = "2026-10-27"  # 距快照日 20261023 为 4 天
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
    assert row.signal == "暂不操作"
    assert "财报禁卖" in row.reason
    assert row.signal_tone == "flat"
    assert row.event_tone == "bear"
    assert row.actionable is False
    assert "财报4d" in row.event_alert
    assert row.put_pick is None  # 否决态绝不挂推荐合约


def test_directional_walls_fallback_when_top_wall_breached() -> None:
    # 现价 248：全链最大 put_wall=250 已在现价上方（失效），应从 wall 分布回退取下方最强 put_gex 240
    ov = {
        "put_wall": {"strike": 250.0},
        "call_wall": {"strike": 240.0},
        "wall": [
            {"strike": None, "put_gex_m": -99.0, "call_gex_m": 99.0},
            {"strike": 230.0, "put_gex_m": -5.2, "call_gex_m": 0.1},
            {"strike": 240.0, "put_gex_m": -12.8, "call_gex_m": 1.0},
            {"strike": 250.0, "put_gex_m": -23.8, "call_gex_m": 8.4},
            {"strike": 260.0, "put_gex_m": -1.0, "call_gex_m": 15.6},
        ],
    }
    pw, cw = _directional_walls(ov, 248.0)
    assert pw == 240.0
    assert cw == 260.0


def test_event_fields_single_line_and_tones() -> None:
    soon_ed = (datetime.now().date() + timedelta(days=20)).strftime("%Y-%m-%d")
    soon_div = (datetime.now().date() + timedelta(days=6)).strftime("%Y-%m-%d")
    _, e_dte, _, div_dte, alert, tone = _event_fields(
        {"earnings_date": soon_ed, "ex_dividend_date": soon_div}
    )
    assert e_dte == 20 and div_dte == 6
    assert " · " in alert and "除息6d" in alert
    assert tone == "warn"


def test_pick_contract_prefers_monthly_opex_and_filters_wide_spread(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    opt_dir = tmp_path / "data" / "options_structure"
    opt_dir.mkdir(parents=True)

    # 2026-10-24 快照：
    # - 20261120 (27d, 第 3 个周五 OpEx): 高 OI(10000)，点差窄 (4.00/4.20)
    # - 20261127 (34d, 离 35d 更近但属周度期权): 低 OI(150)，或点差极宽 (0.50/3.50)
    raw_df = pd.DataFrame(
        [
            {
                "strike": 195.0,
                "right": "P",
                "expiration": "20261120",
                "bid": 4.00,
                "ask": 4.20,
                "openInterest": 15000,
                "volume": 500,
                "impliedVolatility": 0.42,
            },
            {
                "strike": 195.0,
                "right": "P",
                "expiration": "20261127",
                "bid": 4.50,
                "ask": 4.80,
                "openInterest": 180,
                "volume": 10,
                "impliedVolatility": 0.42,
            },
        ]
    )
    raw_df.to_csv(opt_dir / "raw_NVDA_20261024.csv", index=False)

    pick = _pick_contract("NVDA", "20261024", {}, 211.0, 42.0, "P", -0.22, wall_strike=200.0)
    assert pick is not None
    assert pick.expiration == "2026-11-20"  # 优选第 3 个周五标准月度 OpEx，而非 34d 周度
    assert pick.dte == 27
    assert pick.executable is True
    assert pick.annual_roc_pct is not None and pick.annual_roc_pct > 20.0

    # 若盘口点差过宽 (>35%)，应拒绝该档且不回退伪造 BSM 合约
    bad_df = pd.DataFrame(
        [
            {
                "strike": 500.0,
                "right": "C",
                "expiration": "20261120",
                "bid": 1.00,
                "ask": 3.80,
                "openInterest": 400,
                "volume": 5,
                "impliedVolatility": 0.35,
            }
        ]
    )
    bad_df.to_csv(opt_dir / "raw_ISRG_20261024.csv", index=False)
    bad_pick = _pick_contract("ISRG", "20261024", {}, 452.3, 35.0, "C", 0.18, wall_strike=480.0)
    assert bad_pick is None
