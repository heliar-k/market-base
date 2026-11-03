from pathlib import Path

import pandas as pd
import pytest

from src.china_analysis import (
    a_share_block,
    generate_china_overview,
    household_block,
    index_drawdown,
)


def test_household_gap_streak_and_history(tmp_path: Path) -> None:
    directory = tmp_path / "data" / "china"
    directory.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "nbs_income_per_capita_ytd_cny": [10000, 11000, 12000],
            "nbs_income_real_yoy_ytd_pct": [4.0, 4.4, 4.5],
            "nbs_consumption_per_capita_ytd_cny": [7000, 7500, 8000],
            "nbs_consumption_real_yoy_ytd_pct": [2.5, 2.7, 2.8],
        },
        index=pd.to_datetime(["2026-03-31", "2026-06-30", "2026-09-30"]),
    )
    frame.to_csv(directory / "nbs_household.csv", index_label="date")
    result = household_block(tmp_path)
    assert result["gap_pp"] == pytest.approx(1.7)
    assert result["gap_above_2pp_quarters"] == 0
    assert result["triggered"] is False
    assert len(result["history"]["dates"]) == 3


def test_household_gap_does_not_bridge_missing_quarter(tmp_path: Path) -> None:
    directory = tmp_path / "data" / "china"
    directory.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "nbs_income_real_yoy_ytd_pct": [5.0, 5.0, 5.0],
            "nbs_consumption_real_yoy_ytd_pct": [2.0, 2.0, 2.0],
        },
        index=pd.to_datetime(["2026-03-31", "2026-06-30", "2026-12-31"]),
    )
    frame.to_csv(directory / "nbs_household.csv", index_label="date")
    assert household_block(tmp_path)["gap_above_2pp_quarters"] == 1


def test_index_drawdown_keeps_actual_month_end_date() -> None:
    series = pd.Series(
        [100.0, 120.0, 60.0, 55.0],
        index=pd.to_datetime(["2010-06-01", "2010-06-30", "2010-07-30", "2010-08-02"]),
    )
    result = index_drawdown(series)
    assert result["drawdown_pct"] == pytest.approx(54.17, abs=0.01)
    assert result["monthly_drawdown_pct"] == pytest.approx(50.0)
    assert result["monthly_as_of"] == "2010-07-30"
    assert result["scope"] == "历史"


def test_a_share_combines_price_valuation_and_fundamentals(tmp_path: Path) -> None:
    directory = tmp_path / "data" / "china"
    directory.mkdir(parents=True)
    daily = pd.DataFrame(
        {"close": [100.0, 120.0, 110.0]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )
    daily.to_csv(directory / "300308.SZ_daily.csv", index_label="date")
    daily.to_csv(directory / "399006.SZ_daily.csv", index_label="date")
    periods = pd.date_range("2025-03-31", periods=5, freq="QE")
    periods = periods[:-1].append(pd.DatetimeIndex(["2026-06-29"]))
    quarterly = pd.DataFrame(
        {
            "revenue_cny": [100e6] * 4 + [120e6],
            "net_income_cny": [10e6] * 4 + [15e6],
            "inventory_cny": [50e6] * 4 + [60e6],
        },
        index=periods,
    )
    quarterly.to_csv(directory / "300308.SZ_quarterly.csv", index_label="date")
    pd.DataFrame(
        [
            {
                "ticker": "300308.SZ",
                "pe_ttm": 20.0,
                "diluted_eps_ttm_cny": 5.5,
                "eps_period_end": "2026-09-30",
            }
        ]
    ).set_index("ticker").to_csv(directory / "ai_supply_chain_valuation.csv")
    result = a_share_block(tmp_path)
    row = result["rows"][0]
    assert row["drawdown_pct"] == pytest.approx(8.33, abs=0.01)
    assert row["pe_ttm"] == pytest.approx(20.0)
    assert row["revenue_yoy_pct"] == pytest.approx(20.0)
    assert row["net_income_yoy_pct"] == pytest.approx(50.0)


def test_generate_smoke() -> None:
    result = generate_china_overview()
    assert result["household"]["income_real_yoy_pct"] is not None
    assert result["a_shares"]["index"]["drawdown_pct"] >= 0
    assert result["us"]["real_dpi_yoy_pct"] is not None
