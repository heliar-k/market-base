from pathlib import Path

import pandas as pd
import pytest

from src.fetchers.nyfed_household_fetcher import (
    CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT,
    CREDIT_CARD_90PLUS_DELINQ_PCT,
    OUTPUT_COLUMNS,
    _find_latest_workbook_url,
    _quarter_end,
    parse_nyfed_household_xlsx,
    upsert_nyfed_household,
)

_SCRATCH = Path(__file__).resolve().parents[1] / ".scratch" / "nyfed_household"
_WORKBOOK = _SCRATCH / "hhd_c_report_2026q2.xlsx"
_PAGE = _SCRATCH / "background.html"


@pytest.fixture
def official_workbook() -> Path:
    if not _WORKBOOK.exists():
        pytest.skip(f"真实官方 XLSX 不存在: {_WORKBOOK}")
    return _WORKBOOK


@pytest.fixture
def official_page() -> Path:
    if not _PAGE.exists():
        pytest.skip(f"真实官网页面不存在: {_PAGE}")
    return _PAGE


def test_parse_minimal_official_sheet_layout(tmp_path: Path) -> None:
    path = tmp_path / "household.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(
            [
                ["Percent of Balance 90+ Days Delinquent by Loan Type", None, None, None],
                ["03:Q1", "Mortgage", "Auto", "CC"],
                ["03:Q1", 5.0, 3.0, 8.84],
                ["03:Q2", 5.2, 3.2, 9.10],
            ]
        ).to_excel(writer, sheet_name="Page 12 Data", header=False, index=False)
        pd.DataFrame(
            [
                ["New Delinquent (30 or more days delinquent)", None, None, None],
                ["03:Q1", "Mortgage", "Auto", "CC"],
                ["03:Q1", 7.0, 4.0, 12.32],
                ["03:Q2", 7.2, 4.2, 13.00],
            ]
        ).to_excel(writer, sheet_name="Page 13 Data", header=False, index=False)
    df = parse_nyfed_household_xlsx(path)
    assert list(df.columns) == OUTPUT_COLUMNS
    assert df.index.tolist() == ["2003-03-31", "2003-06-30"]
    assert df.iloc[0, 0] == pytest.approx(8.84)
    assert df.iloc[0, 1] == pytest.approx(12.32)


def test_find_latest_workbook_url_selects_latest_quarter() -> None:
    html = (
        '<a href="/medialibrary/interactives/householdcredit/data/xls/'
        'hhd_c_report_2025q4.xlsx">x</a>'
        '<a href="/medialibrary/interactives/householdcredit/data/xls/'
        'hhd_c_report_2026q2.xlsx">x</a>'
        '<a href="/medialibrary/interactives/householdcredit/data/xls/'
        'hhd_c_report_2026q1.xlsx">x</a>'
    )
    assert _find_latest_workbook_url(html).endswith("hhd_c_report_2026q2.xlsx")


def test_quarter_end_dates() -> None:
    assert _quarter_end("03:Q1") == "2003-03-31"
    assert _quarter_end("26:Q2") == "2026-06-30"
    assert _quarter_end("26:Q3") == "2026-09-30"
    assert _quarter_end("26:Q4") == "2026-12-31"
    assert _quarter_end("2026Q2") is None
    assert _quarter_end("not a quarter") is None


def test_find_latest_workbook_url_from_official_page(official_page: Path) -> None:
    html = official_page.read_text(encoding="utf-8")

    url = _find_latest_workbook_url(html)

    assert url == (
        "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/"
        "data/xls/hhd_c_report_2026q2.xlsx"
    )


def test_parse_real_official_workbook(official_workbook: Path) -> None:
    df = parse_nyfed_household_xlsx(official_workbook)

    assert list(df.columns) == OUTPUT_COLUMNS
    assert len(df) == 94
    assert df.index[0] == "2003-03-31"
    assert df.index[-1] == "2026-06-30"
    assert not df.isna().any().any()
    assert df.loc["2003-03-31", CREDIT_CARD_90PLUS_DELINQ_PCT] == pytest.approx(8.84)
    assert df.loc["2003-03-31", CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT] == pytest.approx(12.32)
    assert df.loc["2026-06-30", CREDIT_CARD_90PLUS_DELINQ_PCT] == pytest.approx(12.92)
    assert df.loc["2026-06-30", CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT] == pytest.approx(8.69)


def test_upsert_real_history(official_workbook: Path, tmp_path: Path) -> None:
    df = parse_nyfed_household_xlsx(official_workbook)
    path = tmp_path / "household_credit.csv"

    upsert_nyfed_household(path, df)
    first_write = path.read_bytes()
    upsert_nyfed_household(path, df)

    assert path.read_bytes() == first_write
    stored = pd.read_csv(path)
    assert list(stored.columns) == ["date", *OUTPUT_COLUMNS]
    assert len(stored) == 94
    assert stored["date"].iloc[-1] == "2026-06-30"
    assert stored[CREDIT_CARD_90PLUS_DELINQ_PCT].iloc[-1] == pytest.approx(12.92)
