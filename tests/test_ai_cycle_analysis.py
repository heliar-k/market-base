from pathlib import Path

import pandas as pd
import pytest

from src.ai_cycle_analysis import (
    capex_guidance,
    cloud_disclosures,
    cloud_financials,
    generate_ai_cycle_overview,
    hardware_fundamentals,
    ndx_drawdown,
    negative_streak,
    quarterly_yoy,
    reconcile_single_quarter,
    true_streak,
)


def _cashframes(match: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    qidx = pd.to_datetime(["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"])
    quarterly = pd.DataFrame(
        {
            "Capital Expenditure": [-10.0, -11.0, -12.0, -13.0, -14.0],
            "Operating Cash Flow": [30.0, 31.0, 32.0, 33.0, 34.0],
            "Free Cash Flow": [20.0, 20.0, 20.0, 20.0, 20.0],
        },
        index=qidx,
    )
    annual = pd.DataFrame(
        {
            "Capital Expenditure": [-46.0 if match else -40.0],
            "Operating Cash Flow": [126.0],
            "Free Cash Flow": [80.0],
        },
        index=pd.to_datetime(["2024-12-31"]),
    )
    return quarterly, annual


def test_quarter_reconciliation_accepts_matching_year() -> None:
    quarterly, annual = _cashframes()
    result = reconcile_single_quarter(quarterly, annual)
    assert result["single_quarter_verified"] is True
    assert result["reconciled_years"] == ["2024-12-31"]


def test_quarter_reconciliation_rejects_mismatch() -> None:
    quarterly, annual = _cashframes(match=False)
    assert reconcile_single_quarter(quarterly, annual)["single_quarter_verified"] is False


def test_streaks_stop_at_first_non_matching_observation() -> None:
    values = pd.Series([1.0, -1.0, -2.0, -3.0, 0.0, -4.0])
    assert negative_streak(values) == 1
    assert true_streak(pd.Series([True, True, False, True, True])) == 2


def test_quarterly_yoy_requires_same_quarter_last_year() -> None:
    series = pd.Series(
        [100.0, 110.0, 130.0],
        index=pd.to_datetime(["2025-03-31", "2025-06-30", "2026-09-30"]),
    )
    result = quarterly_yoy(series)
    assert pd.isna(result.iloc[-1])
    valid = pd.Series([100.0, 110.0], index=pd.to_datetime(["2024-06-29", "2025-06-30"]))
    assert quarterly_yoy(valid).iloc[-1] == pytest.approx(10.0)


def test_streak_does_not_bridge_missing_quarter() -> None:
    idx = pd.to_datetime(["2026-01-31", "2026-04-30", "2026-10-31"])
    assert true_streak(pd.Series([True, True, True], index=idx)) == 1
    assert negative_streak(pd.Series([-1.0, -2.0, -3.0], index=idx)) == 1


def test_ndx_drawdown_uses_monthly_close_and_sample_scope() -> None:
    prices = pd.DataFrame(
        {"NDX": [100.0, 120.0, 60.0]},
        index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29"]),
    )
    result = ndx_drawdown(prices)
    assert result["drawdown_pct"] == pytest.approx(50.0)
    assert result["monthly_confirmed"] is True
    assert result["scope"] == "样本期"
    assert result["peak_date"] == "2024-02-29"


def test_ndx_monthly_confirmation_ignores_unfinished_month() -> None:
    prices = pd.DataFrame(
        {"NDX": [100.0, 120.0, 60.0, 55.0]},
        index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29", "2024-04-02"]),
    )
    result = ndx_drawdown(prices)
    assert result["drawdown_pct"] == pytest.approx(54.17, abs=0.01)
    assert result["monthly_drawdown_pct"] == pytest.approx(50.0)
    assert result["monthly_as_of"] == "2024-03-29"


def _write_statement(root: Path, symbol: str, name: str, frame: pd.DataFrame) -> None:
    directory = root / "data" / "financials" / symbol
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_csv(directory / f"{name}.csv", index_label="date")


def test_hardware_growth_streak_requires_two_quarters(tmp_path: Path) -> None:
    idx = pd.date_range("2024-03-31", periods=6, freq="QE")
    balance = pd.DataFrame({"Inventory": [100, 100, 100, 100, 140, 150]}, index=idx)
    income = pd.DataFrame({"Total Revenue": [100, 100, 100, 100, 110, 110]}, index=idx)
    _write_statement(tmp_path, "NVDA", "balance_quarterly", balance)
    _write_statement(tmp_path, "NVDA", "income_quarterly", income)
    result = hardware_fundamentals(tmp_path)
    assert result["rows"][0]["inventory_above_revenue_quarters"] == 2
    assert result["triggered"] == ["NVDA"]


def test_cloud_financials_reads_billions_and_verifies(tmp_path: Path) -> None:
    quarterly, annual = _cashframes()
    fields = ["Capital Expenditure", "Operating Cash Flow", "Free Cash Flow"]
    quarterly[fields] *= 1e9
    annual[fields] *= 1e9
    _write_statement(tmp_path, "MSFT", "cashflow_quarterly", quarterly)
    _write_statement(tmp_path, "MSFT", "cashflow_annual", annual)
    result = cloud_financials(tmp_path)
    row = result["rows"][0]
    assert row["capex_b"] == pytest.approx(14.0)
    assert row["fcf_negative_quarters"] == 0
    assert row["single_quarter_verified"] is True


def test_capex_guidance_compares_next_year_with_actual(tmp_path: Path) -> None:
    annual = pd.DataFrame({"Capital Expenditure": [-100e9]}, index=pd.to_datetime(["2025-12-31"]))
    _write_statement(tmp_path, "AMZN", "cashflow_annual", annual)
    directory = tmp_path / "data" / "ai_cycle"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "AMZN",
                "announced_on": "2026-02-01",
                "guidance_period": "FY2026",
                "minimum_usd": None,
                "maximum_usd": None,
                "point_estimate_usd": 110e9,
                "source_url": "https://www.sec.gov/example",
                "source_excerpt": "reviewed guidance",
            }
        ]
    ).to_csv(directory / "capex_guidance.csv", index=False)
    result = capex_guidance(tmp_path)
    assert result["rows"][0]["guidance_growth_pct"] == pytest.approx(10.0)
    assert result["triggered"] == ["AMZN"]


def test_cloud_disclosure_computes_growth_gap(tmp_path: Path) -> None:
    idx = pd.date_range("2025-03-31", periods=5, freq="QE")
    quarterly = pd.DataFrame({"Capital Expenditure": [-100e9] * 4 + [-120e9]}, index=idx)
    _write_statement(tmp_path, "AMZN", "cashflow_quarterly", quarterly)
    directory = tmp_path / "data" / "ai_cycle"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "AMZN",
                "cloud_unit": "AWS",
                "availability": "absolute_and_growth",
                "form": "10-Q",
                "filing_date": "2026-04-30",
                "accession": "a",
                "fiscal_period": "Q1",
                "period_type": "quarter",
                "period_start": "2026-01-01",
                "period_end": "2026-03-31",
                "revenue_usd": 50e9,
                "prior_year_revenue_usd": 40e9,
                "revenue_growth_pct": 5.0,
                "growth_basis": "yoy",
                "growth_source": "calculated_from_xbrl",
                "xbrl_concept": "Revenue",
                "xbrl_member": "AWS",
                "filing_url": "https://www.sec.gov/example",
                "xbrl_url": "https://www.sec.gov/example.xml",
                "missing_reason": "",
            }
        ]
    ).to_csv(directory / "cloud_disclosures.csv", index=False)
    result = cloud_disclosures(tmp_path)
    row = result["rows"][0]
    assert row["capex_yoy_pct"] == pytest.approx(20.0)
    assert row["growth_gap_pp"] == pytest.approx(-15.0)
    assert row["revenue_below_capex_quarters"] == 1
    assert row["capex_revenue_ratio_pct"] == pytest.approx(240.0)


def test_generate_smoke() -> None:
    result = generate_ai_cycle_overview()
    assert len(result["cloud"]["rows"]) == 4
    assert all(row["single_quarter_verified"] for row in result["cloud"]["rows"])
    assert not result["cloud"]["stale_symbols"]
    assert result["ndx"]["drawdown_pct"] >= 0
    meta = next(row for row in result["cloud_disclosure"]["rows"] if row["symbol"] == "META")
    assert meta["revenue_below_capex_quarters"] is None
    assert {layer["key"] for layer in result["layers"]} == {
        "leading",
        "synchronous",
        "confirmation",
    }
