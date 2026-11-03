import pandas as pd
import pytest

from src.fetchers.nbs_monthly_fetcher import (
    CPI,
    OUTPUT_COLUMNS,
    PPI,
    REAL_ESTATE,
    RETAIL,
    ReleaseRef,
    is_access_challenge,
    observation_end,
    parse_index,
    parse_page_count,
    parse_release,
    releases_to_frame,
)

# 以下 HTML 均摘自 stats.gov.cn 官方页面，仅省略与解析无关的标签属性。
INDEX_HTML = """
<ul class="list">
<li><a target="_blank" title="Investment in Real Estate Development from January to September 2026"
 href="./202610/t20261020_1965663.html">9.Investment in Real Estate Development</a></li>
<li><a target="_blank" title="Total Retail Sales of Consumer Goods from January to September 2026"
 href="./202610/t20261020_1965662.html">10.Total Retail Sales of Consumer Goods</a></li>
<li><a target="_blank" title="Consumer Price Index in September 2026"
 href="./202610/t20261015_1965500.html">17.Consumer Price Index</a></li>
<li><a target="_blank" title="Industrial Producer Price Indexes in September 2026"
 href="./202610/t20261015_1965499.html">18.Industrial Producer Price Indexes</a></li>
<li><a target="_blank" title="Producer Price Index in the Industrial Sector for May 2024"
 href="./202406/t20240625_1955164.html">487.Producer Price Index</a></li>
<li><a title="Energy Production in September 2026"
 href="./202610/t20261020_1965661.html">11.Energy</a></li>
</ul>
<script>var currentPage = 0; var countPage = 34;</script>
"""

RETAIL_202609_HTML = """
<table><tr>
<td><p><span>Total retail sales of consumer goods</span></p></td>
<td><p><span>42433</span></p></td><td><p><span>1.1</span></p></td>
<td><p><span>370001</span></p></td><td><p><span>1.1</span></p></td>
</tr></table>
"""
REAL_ESTATE_202609_HTML = """
<table>
<tr><td>Investment in real estate development (100 million yuan)</td>
<td>53364</td><td>-20.6</td></tr>
<tr><td>Floor space of newly built commercial buildings sold (10,000 sq.m)</td>
<td>56483</td><td>-13.2</td></tr>
</table>
"""
CPI_202609_HTML = """
<table><tr><td>Consumer Price Index</td><td>0.2</td><td>0.9</td><td>0.9</td></tr></table>
"""
PPI_202609_HTML = """
<table><tr><td>I. Producer Price Indexes for Industrial Products</td>
<td>1.2</td><td>5.0</td><td>2.3</td></tr></table>
"""
CPI_202601_HTML = """
<table><tr><td>Consumer Price Index</td><td>0.2</td><td>0.2</td></tr></table>
"""
PPI_202601_HTML = """
<table><tr><td>I. Producer Price Indexes for Industrial Products</td>
<td>0.4</td><td>-1.4</td></tr></table>
"""
PPI_202405_HTML = """
<table><tr><td>I. Producer Prices for Industrial Products</td>
<td>0.2</td><td>-1.4</td><td>-2.4</td></tr></table>
"""
RETAIL_202502_HTML = """
<table><tr><td>Total retail sales of consumer goods</td><td>83731</td><td>4.0</td></tr></table>
"""
RETAIL_202404_HTML = """
<table><tr>
<td>Total retail sales of consumer goods</td><td>35699</td><td>2.3</td><td>156026</td><td>4.1</td>
</tr></table>
"""
REAL_ESTATE_202404_HTML = """
<table>
<tr><td>Investment in real estate development (100 million yuan)</td>
<td>30928</td><td>-9.8</td></tr>
<tr><td>Floor space of newly built commercial buildings sold (10,000 sq.m)</td>
<td>29252</td><td>-20.2</td></tr>
</table>
"""


def _ref(kind: str, title: str) -> ReleaseRef:
    return ReleaseRef(kind, title, "https://www.stats.gov.cn/english/PressRelease/example.html")


def test_parse_official_index_and_page_count() -> None:
    refs = parse_index(INDEX_HTML)

    assert parse_page_count(INDEX_HTML) == 34
    assert [(ref.kind, ref.url.rsplit("/", 1)[-1]) for ref in refs] == [
        (REAL_ESTATE, "t20261020_1965663.html"),
        (RETAIL, "t20261020_1965662.html"),
        (CPI, "t20261015_1965500.html"),
        (PPI, "t20261015_1965499.html"),
        (PPI, "t20240625_1955164.html"),
    ]
    assert refs[0].url == (
        "https://www.stats.gov.cn/english/PressRelease/202610/t20261020_1965663.html"
    )


def test_parse_latest_retail_release() -> None:
    ref = _ref(RETAIL, "Total Retail Sales of Consumer Goods from January to September 2026")

    record = parse_release(ref, RETAIL_202609_HTML)

    assert record == {
        "date": pd.Timestamp("2026-09-30"),
        "nbs_retail_sales_yoy_pct": 1.1,
        "nbs_retail_sales_ytd_yoy_pct": 1.1,
    }


def test_parse_latest_real_estate_release() -> None:
    ref = _ref(REAL_ESTATE, "Investment in Real Estate Development from January to September 2026")

    record = parse_release(ref, REAL_ESTATE_202609_HTML)

    assert record == {
        "date": pd.Timestamp("2026-09-30"),
        "nbs_real_estate_investment_ytd_yoy_pct": -20.6,
        "nbs_new_commercial_floor_space_sold_ytd_yoy_pct": -13.2,
    }


@pytest.mark.parametrize(
    ("kind", "title", "html", "expected"),
    [
        (
            CPI,
            "Consumer Price Index in September 2026",
            CPI_202609_HTML,
            {"nbs_cpi_yoy_pct": 0.9, "nbs_cpi_ytd_yoy_pct": 0.9},
        ),
        (
            PPI,
            "Industrial Producer Price Indexes in September 2026",
            PPI_202609_HTML,
            {"nbs_ppi_yoy_pct": 5.0, "nbs_ppi_ytd_yoy_pct": 2.3},
        ),
    ],
)
def test_parse_latest_price_releases(kind, title, html, expected) -> None:
    record = parse_release(_ref(kind, title), html)

    assert record["date"] == pd.Timestamp("2026-09-30")
    assert record.items() >= expected.items()


@pytest.mark.parametrize(
    ("kind", "title", "html", "column", "expected"),
    [
        (CPI, "Consumer Price Index in January 2026", CPI_202601_HTML, "nbs_cpi", 0.2),
        (
            PPI,
            "Industrial Producer Price Indexes in January 2026",
            PPI_202601_HTML,
            "nbs_ppi",
            -1.4,
        ),
    ],
)
def test_january_price_growth_is_both_monthly_and_cumulative(
    kind, title, html, column, expected
) -> None:
    record = parse_release(_ref(kind, title), html)

    assert record[f"{column}_yoy_pct"] == expected
    assert record[f"{column}_ytd_yoy_pct"] == expected


def test_parse_2024_ppi_title_and_table_wording() -> None:
    ref = _ref(PPI, "Producer Price Index in the Industrial Sector for May 2024")

    record = parse_release(ref, PPI_202405_HTML)

    assert record == {
        "date": pd.Timestamp("2024-05-31"),
        "nbs_ppi_yoy_pct": -1.4,
        "nbs_ppi_ytd_yoy_pct": -2.4,
    }


def test_january_february_retail_keeps_only_cumulative_growth() -> None:
    ref = _ref(RETAIL, "Total Retail Sales of Consumer Goods from January to February 2025")

    record = parse_release(ref, RETAIL_202502_HTML)

    assert record == {
        "date": pd.Timestamp("2025-02-28"),
        "nbs_retail_sales_ytd_yoy_pct": 4.0,
    }


@pytest.mark.parametrize(
    ("kind", "title", "html", "expected"),
    [
        (
            RETAIL,
            "Total Retail Sales of Consumer Goods in April 2024",
            RETAIL_202404_HTML,
            {"nbs_retail_sales_yoy_pct": 2.3, "nbs_retail_sales_ytd_yoy_pct": 4.1},
        ),
        (
            REAL_ESTATE,
            "Investment in Real Estate Development for Jan-Apr 2024",
            REAL_ESTATE_202404_HTML,
            {
                "nbs_real_estate_investment_ytd_yoy_pct": -9.8,
                "nbs_new_commercial_floor_space_sold_ytd_yoy_pct": -20.2,
            },
        ),
    ],
)
def test_parse_2024_release_formats(kind, title, html, expected) -> None:
    record = parse_release(_ref(kind, title), html)

    assert record["date"] == pd.Timestamp("2024-04-30")
    assert record.items() >= expected.items()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Total Retail Sales of Consumer Goods in the First Half of 2026", "2026-06-30"),
        ("Investment in Real Estate Development in 2024", "2024-12-31"),
        ("Investment in Real Estate Development for January-August 2025", "2025-08-31"),
        ("Consumer Price Index for June 2024", "2024-06-30"),
    ],
)
def test_observation_end_title_variants(title, expected) -> None:
    assert observation_end(title) == pd.Timestamp(expected)


def test_combine_release_records_by_observation_end() -> None:
    records = [
        parse_release(
            _ref(RETAIL, "Total Retail Sales of Consumer Goods from January to September 2026"),
            RETAIL_202609_HTML,
        ),
        parse_release(_ref(CPI, "Consumer Price Index in September 2026"), CPI_202609_HTML),
        parse_release(
            _ref(PPI, "Industrial Producer Price Indexes in September 2026"), PPI_202609_HTML
        ),
        parse_release(
            _ref(
                REAL_ESTATE, "Investment in Real Estate Development from January to September 2026"
            ),
            REAL_ESTATE_202609_HTML,
        ),
    ]

    df = releases_to_frame(records)

    assert df.index.equals(pd.DatetimeIndex(["2026-09-30"], name="date"))
    assert df.columns.tolist() == OUTPUT_COLUMNS
    assert df.notna().sum().sum() == 8


def test_access_challenge_is_not_mistaken_for_an_index() -> None:
    challenge = "<noscript>Please enable JavaScript</noscript><script>var c='/WZWSREL';</script>"

    assert is_access_challenge(challenge)
    assert not is_access_challenge(INDEX_HTML)


def test_parse_rejects_missing_indicator_row() -> None:
    ref = _ref(CPI, "Consumer Price Index in September 2026")

    with pytest.raises(ValueError, match="指标行数量异常"):
        parse_release(
            ref, "<table><tr><td>Rural</td><td>0.4</td><td>0.8</td><td>0.7</td></tr></table>"
        )
