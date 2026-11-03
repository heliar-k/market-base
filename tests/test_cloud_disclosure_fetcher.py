import pandas as pd
import pytest

from src.fetchers.cloud_disclosure_fetcher import (
    capex_guidance_event,
    capex_guidance_frame,
    parse_filing_disclosures,
)

REVENUE = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
AMZN_Q3_TEXT = (
    "AWS sales increased 39% in Q3 2026, and 35% for the nine months ended "
    "September 30, 2026 compared to the comparable prior year periods."
)
MSFT_Q2_TEXT = (
    "Intelligent Cloud Revenue increased $7.4 billion or 29%. "
    "Azure and other cloud services revenue grew 39% driven by demand for our portfolio. "
    "Six Months Ended December 31, 2025 Compared with Six Months Ended December 31, 2024. "
    "Intelligent Cloud Revenue increased $14.2 billion or 29%. "
    "Azure and other cloud services revenue grew 40% driven by demand for our portfolio."
)
MSFT_LATEST_TEXT = (
    "Azure revenue increased 48%. Agents and Infra Revenue increased $15.1 billion or 25%. "
    "Azure revenue increased $10.8 billion or 48% driven by demand for services."
)


def _xbrl(
    contexts: list[tuple[str, str, str, str, str]],
    facts: list[tuple[str, str, str]],
    *,
    document_end: str,
    fiscal_period: str = "Q3",
) -> bytes:
    context_xml = []
    for context_id, start, end, axis, member in contexts:
        segment = ""
        if axis:
            segment = (
                "<xbrli:segment><xbrldi:explicitMember "
                f'dimension="{axis}">{member}</xbrldi:explicitMember></xbrli:segment>'
            )
        context_xml.append(
            f'<xbrli:context id="{context_id}"><xbrli:entity>'
            '<xbrli:identifier scheme="http://www.sec.gov/CIK">0000000000</xbrli:identifier>'
            f"{segment}</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>"
            f"<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>"
        )
    context_xml.append(
        '<xbrli:context id="document"><xbrli:entity>'
        '<xbrli:identifier scheme="http://www.sec.gov/CIK">0000000000</xbrli:identifier>'
        "</xbrli:entity><xbrli:period><xbrli:instant>"
        f"{document_end}</xbrli:instant></xbrli:period></xbrli:context>"
    )
    fact_xml = [
        f'<{concept} contextRef="{context_id}" unitRef="usd" decimals="-6">{value}</{concept}>'
        for concept, context_id, value in facts
    ]
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:us-gaap="http://fasb.org/us-gaap/2026" '
        'xmlns:srt="http://fasb.org/srt/2026" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2026" '
        'xmlns:amzn="http://www.amazon.com/20260930" '
        'xmlns:goog="http://www.google.com/20260930" '
        'xmlns:msft="http://www.microsoft.com/20260930" '
        'xmlns:meta="http://www.meta.com/20260930">'
        f'<dei:DocumentPeriodEndDate contextRef="document">'
        f"{document_end}</dei:DocumentPeriodEndDate>"
        f'<dei:DocumentFiscalPeriodFocus contextRef="document">'
        f"{fiscal_period}</dei:DocumentFiscalPeriodFocus>"
        + "".join(context_xml)
        + "".join(fact_xml)
        + "</xbrli:xbrl>"
    ).encode()


def _parse(symbol: str, xbrl: bytes, text: str, **kwargs) -> pd.DataFrame:
    metadata = {
        "form": "10-Q",
        "filing_date": "2026-10-30",
        "filing_url": f"https://www.sec.gov/{symbol}/filing.htm",
        "xbrl_url": f"https://www.sec.gov/{symbol}/filing_htm.xml",
        "accession": "0000000000-26-000001",
        **kwargs,
    }
    return parse_filing_disclosures(symbol, xbrl, text, **metadata)


def _row(df: pd.DataFrame, period_type: str) -> pd.Series:
    selected = df[df["period_type"] == period_type]
    assert len(selected) == 1
    return selected.iloc[0]


def test_amazon_xbrl_separates_quarter_and_ytd_and_calculates_growth() -> None:
    contexts = [
        (
            "q",
            "2026-07-01",
            "2026-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "amzn:AmazonWebServicesSegmentMember",
        ),
        (
            "y",
            "2026-01-01",
            "2026-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "amzn:AmazonWebServicesSegmentMember",
        ),
        (
            "pq",
            "2025-07-01",
            "2025-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "amzn:AmazonWebServicesSegmentMember",
        ),
        (
            "py",
            "2025-01-01",
            "2025-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "amzn:AmazonWebServicesSegmentMember",
        ),
    ]
    facts = [
        (REVENUE, "q", "45984000000"),
        (REVENUE, "y", "125803000000"),
        (REVENUE, "pq", "33006000000"),
        (REVENUE, "py", "93146000000"),
    ]
    df = _parse("AMZN", _xbrl(contexts, facts, document_end="2026-09-30"), AMZN_Q3_TEXT)

    quarter = _row(df, "quarter")
    ytd = _row(df, "ytd")
    assert quarter["revenue_usd"] == 45_984_000_000
    assert quarter["prior_year_revenue_usd"] == 33_006_000_000
    assert quarter["revenue_growth_pct"] == pytest.approx(39.3201, abs=0.0001)
    assert ytd["revenue_usd"] == 125_803_000_000
    assert ytd["prior_year_revenue_usd"] == 93_146_000_000
    assert ytd["revenue_growth_pct"] == pytest.approx(35.0600, abs=0.0001)
    assert set(df["availability"]) == {"absolute_and_growth"}
    assert set(df["growth_source"]) == {"calculated_from_xbrl"}
    assert set(df["fiscal_period"]) == {"Q3"}


def test_google_cloud_uses_segment_member_not_consolidated_revenue() -> None:
    contexts = [
        (
            "q",
            "2026-07-01",
            "2026-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "goog:GoogleCloudMember",
        ),
        (
            "pq",
            "2025-07-01",
            "2025-09-30",
            "us-gaap:StatementBusinessSegmentsAxis",
            "goog:GoogleCloudMember",
        ),
        ("total", "2026-07-01", "2026-09-30", "", ""),
    ]
    facts = [
        (REVENUE, "q", "30709000000"),
        (REVENUE, "pq", "15157" + "000000"),
        (REVENUE, "total", "127604000000"),
    ]
    df = _parse("GOOG", _xbrl(contexts, facts, document_end="2026-09-30"), "")

    row = _row(df, "quarter")
    assert row["cloud_unit"] == "Google Cloud"
    assert row["revenue_usd"] == 30_709_000_000
    assert row["prior_year_revenue_usd"] == 15_157_000_000
    assert row["revenue_growth_pct"] == pytest.approx(102.6061, abs=0.0001)
    assert row["xbrl_member"] == "goog:GoogleCloudMember"


def test_azure_growth_only_leaves_absolute_empty_and_ignores_intelligent_cloud() -> None:
    contexts = [
        ("q", "2025-10-01", "2025-12-31", "", ""),
        ("y", "2025-07-01", "2025-12-31", "", ""),
        (
            "icq",
            "2025-10-01",
            "2025-12-31",
            "us-gaap:StatementBusinessSegmentsAxis",
            "msft:IntelligentCloudMember",
        ),
        (
            "icy",
            "2025-07-01",
            "2025-12-31",
            "us-gaap:StatementBusinessSegmentsAxis",
            "msft:IntelligentCloudMember",
        ),
    ]
    facts = [
        (REVENUE, "q", "81273000000"),
        (REVENUE, "y", "158946000000"),
        (REVENUE, "icq", "32907000000"),
        (REVENUE, "icy", "63804000000"),
    ]
    df = _parse(
        "MSFT",
        _xbrl(contexts, facts, document_end="2025-12-31", fiscal_period="Q2"),
        MSFT_Q2_TEXT,
        filing_date="2026-01-28",
    )

    quarter = _row(df, "quarter")
    ytd = _row(df, "ytd")
    assert quarter["cloud_unit"] == "Azure and other cloud services"
    assert pd.isna(quarter["revenue_usd"])
    assert pd.isna(quarter["prior_year_revenue_usd"])
    assert quarter["revenue_growth_pct"] == 39
    assert ytd["revenue_growth_pct"] == 40
    assert set(df["availability"]) == {"growth_only"}
    assert set(df["growth_source"]) == {"sec_filing_text"}
    assert quarter["xbrl_member"] == ""


def test_azure_absolute_is_used_when_explicitly_tagged_and_reported_growth_is_preserved() -> None:
    contexts = [
        ("q", "2026-07-01", "2026-09-30", "srt:ProductOrServiceAxis", "msft:AzureMember"),
        ("pq", "2025-07-01", "2025-09-30", "srt:ProductOrServiceAxis", "msft:AzureMember"),
    ]
    facts = [(REVENUE, "q", "33165000000"), (REVENUE, "pq", "22384000000")]
    df = _parse(
        "MSFT",
        _xbrl(contexts, facts, document_end="2026-09-30", fiscal_period="Q1"),
        MSFT_LATEST_TEXT,
        filing_date="2026-10-28",
    )

    row = _row(df, "quarter")
    assert row["cloud_unit"] == "Azure"
    assert row["revenue_usd"] == 33_165_000_000
    assert row["prior_year_revenue_usd"] == 22_384_000_000
    assert row["revenue_growth_pct"] == 48
    assert row["growth_source"] == "sec_filing_text"
    assert row["xbrl_member"] == "msft:AzureMember"


def test_meta_has_explicit_missing_rows_without_inventing_cloud_revenue() -> None:
    contexts = [
        ("q", "2026-07-01", "2026-09-30", "", ""),
        ("y", "2026-01-01", "2026-09-30", "", ""),
    ]
    facts = [(REVENUE, "q", "64601000000"), (REVENUE, "y", "181712000000")]
    df = _parse("META", _xbrl(contexts, facts, document_end="2026-09-30"), "")

    assert len(df) == 2
    assert set(df["availability"]) == {"not_disclosed"}
    assert df["revenue_usd"].isna().all()
    assert df["revenue_growth_pct"].isna().all()
    assert df["cloud_unit"].eq("").all()
    assert df["missing_reason"].str.contains("cloud segment").all()


def test_capex_guidance_requires_reviewed_values_and_keeps_source_text() -> None:
    event = capex_guidance_event(
        symbol="META",
        announced_on="2026-10-22",
        guidance_period="FY2026",
        minimum_usd=135_000_000_000,
        maximum_usd=145_000_000_000,
        source_url="https://www.sec.gov/Archives/edgar/data/1326801/000162828026066773/meta-20260930.htm",
        source_excerpt=(
            "We anticipate making capital expenditures of approximately $135 billion "
            "to $145 billion in 2026 to support our AI efforts and core business."
        ),
    )
    df = capex_guidance_frame([event])

    assert df.iloc[0]["minimum_usd"] == 135_000_000_000
    assert df.iloc[0]["maximum_usd"] == 145_000_000_000
    assert df.iloc[0]["currency"] == "USD"
    assert df.iloc[0]["extraction_method"] == "manual_verified_issuer_disclosure"
    assert "source_excerpt" in df.columns


def test_capex_guidance_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="minimum_usd"):
        capex_guidance_event(
            symbol="AMZN",
            announced_on="2026-10-30",
            guidance_period="FY2026",
            minimum_usd=145_000_000_000,
            maximum_usd=135_000_000_000,
            source_url="https://www.sec.gov/example.htm",
            source_excerpt="reviewed issuer guidance",
        )
