import pandas as pd
import pytest

from src.sell_put import analyze_puts


def test_dte_uses_calendar_days_and_preserves_annualized_return():
    today = pd.Timestamp.now(tz="America/New_York").date()
    expiration = (pd.Timestamp(today) + pd.Timedelta(days=30)).strftime("%Y%m%d")
    gex = pd.DataFrame(
        [
            {
                "right": "P",
                "strike": 90.0,
                "expiration": expiration,
                "openInterest": 100,
                "bid": 4.0,
                "ask": 4.2,
                "delta": -0.2,
                "impliedVolatility": 0.3,
            }
        ]
    )
    out = analyze_puts(gex, 100.0, 2.0, "TEST")
    assert out["dte"].iloc[0] == 30
    expected = 4.1 / 90.0 / 30 * 365 * 100
    assert out["年化%"].iloc[0] == expected


def test_missing_delta_is_filled_with_bsm_put_delta(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import src.sell_put as sp
    from src.pricing import bs_greeks, years_to_expiration

    now = datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(sp, "now_et", lambda: now)
    monkeypatch.setattr(sp, "et_today", lambda: now.date())

    # 仅含 IBKR 纯路径的 iv 列（无 impliedVolatility 列），且一行缺 delta、一行已有 delta
    gex = pd.DataFrame(
        [
            {
                "right": "P",
                "strike": 95.0,
                "expiration": "20261126",
                "openInterest": 100,
                "bid": 2.0,
                "ask": 2.2,
                "iv": 0.25,
                "delta": float("nan"),
            },
            {
                "right": "P",
                "strike": 90.0,
                "expiration": "20261126",
                "openInterest": 100,
                "bid": 1.0,
                "ask": 1.2,
                "iv": 0.28,
                "delta": -0.15,
            },
        ]
    )
    out = analyze_puts(gex, 100.0, 2.0, "TEST").set_index("strike")
    t = years_to_expiration("20261126", now)
    _, dc = bs_greeks(100.0, 95.0, t, 0.25)
    assert abs(out.loc[95.0, "delta"] - (dc - 1.0)) < 1e-12
    assert out.loc[95.0, "iv%"] == pytest.approx(25.0)
    # 已有非空 delta 不被 BSM 覆盖
    assert out.loc[90.0, "delta"] == -0.15
    assert out.loc[90.0, "iv%"] == pytest.approx(28.0)
