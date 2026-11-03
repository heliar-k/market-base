import os

import pandas as pd
import pytest

from src.fetchers.sec_cashflow_fetcher import (
    CAPEX_TAGS,
    CFO_TAG,
    SCRATCH_DIR,
    companyfacts_to_quarterly,
    fetch_cashflow,
)


def _fact(
    start: str | None,
    end: str,
    value: int,
    fp: str,
    form: str,
    filed: str,
    accession: str,
    frame: str | None = None,
) -> dict:
    fact = {
        "end": end,
        "val": value,
        "accn": accession,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start is not None:
        fact["start"] = start
    if frame is not None:
        fact["frame"] = frame
    return fact


def _payload(cik: int, capex_tag: str, cfo_facts: list[dict], capex_facts: list[dict]) -> dict:
    return {
        "cik": cik,
        "facts": {
            "us-gaap": {
                CFO_TAG: {"units": {"USD": cfo_facts}},
                capex_tag: {"units": {"USD": capex_facts}},
            }
        },
    }


def _amazon_payload() -> dict:
    q1 = ("2026-01-01", "2026-03-31", "Q1", "2026-04-30", "0001018724-26-000014")
    q2 = ("2026-01-01", "2026-06-30", "Q2", "2026-07-31", "0001018724-26-000026")
    q3 = ("2026-01-01", "2026-09-30", "Q3", "2026-10-30", "0001018724-26-000040")
    cfo = [
        _fact(q1[0], q1[1], 26_032_000_000, q1[2], "10-Q", q1[3], q1[4], "CY2026Q1"),
        _fact(q2[0], q2[1], 71_419_000_000, q2[2], "10-Q", q2[3], q2[4]),
        _fact(q3[0], q3[1], 113_841_000_000, q3[2], "10-Q", q3[3], q3[4]),
        # 同一 Q2 申报里的直接单季与 TTM 均不得进入 YTD 差分。
        _fact("2026-04-01", "2026-06-30", 45_387_000_000, "Q2", "10-Q", q2[3], q2[4], "CY2026Q2"),
        _fact("2025-07-01", "2026-06-30", 161_403_000_000, "Q2", "10-Q", q2[3], q2[4]),
        _fact("2026-07-01", "2026-09-30", 42_422_000_000, "Q3", "10-Q", q3[3], q3[4], "CY2026Q3"),
        _fact("2025-10-01", "2026-09-30", 168_300_000_000, "Q3", "10-Q", q3[3], q3[4], "CY2026"),
        _fact(None, "2026-09-30", 999, "Q3", "10-Q", q3[3], q3[4]),
    ]
    capex = [
        _fact(q1[0], q1[1], 44_203_000_000, q1[2], "10-Q", q1[3], q1[4], "CY2026Q1"),
        _fact(q2[0], q2[1], 98_411_000_000, q2[2], "10-Q", q2[3], q2[4]),
        _fact(q3[0], q3[1], 175_670_000_000, q3[2], "10-Q", q3[3], q3[4]),
        _fact("2026-04-01", "2026-06-30", 54_208_000_000, "Q2", "10-Q", q2[3], q2[4], "CY2026Q2"),
        _fact("2025-07-01", "2026-06-30", 173_028_000_000, "Q2", "10-Q", q2[3], q2[4]),
        _fact("2026-07-01", "2026-09-30", 77_259_000_000, "Q3", "10-Q", q3[3], q3[4], "CY2026Q3"),
        _fact("2025-10-01", "2026-09-30", 215_192_000_000, "Q3", "10-Q", q3[3], q3[4], "CY2026"),
        _fact(None, "2026-09-30", 999, "Q3", "10-Q", q3[3], q3[4]),
    ]
    return _payload(1_018_724, CAPEX_TAGS["AMZN"], cfo, capex)


def test_amazon_2026_q2_q3_use_same_fiscal_year_ytd_differences() -> None:
    df = companyfacts_to_quarterly(_amazon_payload(), "AMZN")

    assert df["date"].tolist() == ["2026-03-31", "2026-06-30", "2026-09-30"]
    assert df["Operating Cash Flow"].tolist() == [
        26_032_000_000,
        45_387_000_000,
        42_422_000_000,
    ]
    assert df["Capital Expenditure"].tolist() == [
        -44_203_000_000,
        -54_208_000_000,
        -77_259_000_000,
    ]
    assert df["Free Cash Flow"].tolist() == [
        -18_171_000_000,
        -8_821_000_000,
        -34_837_000_000,
    ]
    assert df["period_start"].tolist() == ["2026-01-01", "2026-04-01", "2026-07-01"]
    assert df["derivation"].tolist() == [
        "Q1 YTD",
        "Q2 YTD - Q1 YTD",
        "Q3 YTD - Q2 YTD",
    ]
    latest = df.iloc[-1]
    assert latest["accession"] == "0001018724-26-000040"
    assert latest["form"] == "10-Q"
    assert latest["filing_date"] == "2026-10-30"
    assert latest["source"].endswith("CIK0001018724.json")
    assert latest["capex_tag"] == "PaymentsToAcquireProductiveAssets"


def test_microsoft_fiscal_q4_and_next_q1_reconcile_to_official_annual() -> None:
    current = [
        ("2025-09-30", "Q1", "10-Q", "2025-10-29", "0001193125-25-256321"),
        ("2025-12-31", "Q2", "10-Q", "2026-01-28", "0001193125-26-027207"),
        ("2026-03-31", "Q3", "10-Q", "2026-04-29", "0001193125-26-191507"),
        ("2026-06-30", "FY", "10-K", "2026-07-29", "0001193125-26-323660"),
    ]
    cfo_values = [45_057_000_000, 80_815_000_000, 127_494_000_000, 182_935_000_000]
    capex_values = [34_939_000_000, 49_270_000_000, 80_146_000_000, 115_948_000_000]
    cfo = [
        _fact("2025-07-01", end, value, fp, form, filed, accession)
        for (end, fp, form, filed, accession), value in zip(current, cfo_values, strict=True)
    ]
    capex = [
        _fact("2025-07-01", end, value, fp, form, filed, accession)
        for (end, fp, form, filed, accession), value in zip(current, capex_values, strict=True)
    ]
    next_q1 = ("2026-07-01", "2026-09-30", "Q1", "2026-10-28", "0001193125-26-436133")
    cfo.append(_fact(next_q1[0], next_q1[1], 58_000_000_000, "Q1", "10-Q", next_q1[3], next_q1[4]))
    capex.append(
        _fact(next_q1[0], next_q1[1], 39_367_000_000, "Q1", "10-Q", next_q1[3], next_q1[4])
    )
    df = companyfacts_to_quarterly(_payload(789_019, CAPEX_TAGS["MSFT"], cfo, capex), "MSFT")

    fiscal_2026 = df.iloc[:4]
    assert fiscal_2026["fiscal_period"].tolist() == ["Q1", "Q2", "Q3", "Q4"]
    assert fiscal_2026.iloc[-1]["derivation"] == "FY YTD - Q3 YTD"
    assert fiscal_2026["Operating Cash Flow"].sum() == 182_935_000_000
    assert fiscal_2026["Capital Expenditure"].sum() == -115_948_000_000
    assert fiscal_2026["Free Cash Flow"].sum() == 66_987_000_000
    next_row = df.iloc[-1]
    assert next_row["date"] == "2026-09-30"
    assert next_row["Operating Cash Flow"] == 58_000_000_000
    assert next_row["Capital Expenditure"] == -39_367_000_000
    assert next_row["Free Cash Flow"] == 18_633_000_000


def test_missing_q1_does_not_turn_annual_or_later_ytd_into_a_quarter() -> None:
    payload = _amazon_payload()
    for tag in (CFO_TAG, CAPEX_TAGS["AMZN"]):
        facts = payload["facts"]["us-gaap"][tag]["units"]["USD"]
        facts[:] = [
            fact
            for fact in facts
            if not (fact.get("start") == "2026-01-01" and fact["end"] == "2026-03-31")
        ]
        annual_value = 139_514_000_000 if tag == CFO_TAG else 131_819_000_000
        facts.append(
            _fact(
                "2026-01-01",
                "2026-12-31",
                annual_value,
                "FY",
                "10-K",
                "2027-02-05",
                "0001018724-27-000001",
            )
        )

    df = companyfacts_to_quarterly(payload, "AMZN")
    assert df.empty


@pytest.mark.skipif(
    os.environ.get("SEC_CASHFLOW_LIVE") != "1", reason="设置 SEC_CASHFLOW_LIVE=1 运行官方联网测试"
)
@pytest.mark.parametrize(
    ("symbol", "cfo", "capex", "fcf", "accession", "filing_date"),
    [
        (
            "MSFT",
            58_000_000_000,
            -39_367_000_000,
            18_633_000_000,
            "0001193125-26-436133",
            "2026-10-28",
        ),
        (
            "GOOG",
            42_290_000_000,
            -51_355_000_000,
            -9_065_000_000,
            "0001652044-26-000084",
            "2026-10-29",
        ),
        (
            "AMZN",
            42_422_000_000,
            -77_259_000_000,
            -34_837_000_000,
            "0001018724-26-000040",
            "2026-10-30",
        ),
        (
            "META",
            32_785_000_000,
            -33_942_000_000,
            -1_157_000_000,
            "0001628280-26-066773",
            "2026-10-22",
        ),
    ],
)
def test_real_sec_companyfacts_latest_quarter(
    symbol: str, cfo: int, capex: int, fcf: int, accession: str, filing_date: str
) -> None:
    df = fetch_cashflow(symbol)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(SCRATCH_DIR / f"{symbol}_cashflow_quarterly.csv", index=False)

    latest = df.iloc[-1]
    assert latest["date"] == "2026-09-30"
    assert latest["Operating Cash Flow"] == cfo
    assert latest["Capital Expenditure"] == capex
    assert latest["Free Cash Flow"] == fcf
    assert latest["Free Cash Flow"] == latest["Operating Cash Flow"] + latest["Capital Expenditure"]
    assert latest["accession"] == accession
    assert latest["form"] == "10-Q"
    assert latest["filing_date"] == filing_date
    assert latest["source"].startswith("https://data.sec.gov/")
    assert pd.notna(latest["cfo_tag"])
