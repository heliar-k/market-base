import pandas as pd

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
