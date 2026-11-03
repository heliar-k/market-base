# Challenger 官方归档与报告解析测试；全部使用本地 HTML 样本，不访问网络。

import pytest

from src.fetchers.challenger_fetcher import (
    _make_frame,
    parse_archive,
    parse_report,
)


def _report_html(body: str, published: str, pdf: bool = True) -> str:
    pdf_link = (
        '<a href="/wp-content/uploads/2025/11/Challenger-Report-October-2025.pdf">PDF</a>'
        if pdf
        else ""
    )
    return (
        f'<html><head><meta property="article:published_time" content="{published}T06:30:00+00:00">'
        f"</head><body><article><p>{body}</p>{pdf_link}</article></body></html>"
    )


def test_parse_archive_links_and_first_next() -> None:
    html = """
    <link rel="next" href="/blog/category/job-cuts-report/page/2/">
    <link rel="next" href="/blog/page/2/">
    <h2><a href="/blog/october-report/">October report</a></h2>
    <h2><a href="https://example.com/blog/not-official/">External</a></h2>
    <h2><a href="/blog/october-report/#duplicate">Duplicate</a></h2>
    """
    links, next_url = parse_archive(html)
    assert links == ["https://www.challengergray.com/blog/october-report/"]
    assert next_url == "https://www.challengergray.com/blog/category/job-cuts-report/page/2/"


def test_archive_stops_at_global_blog_pagination() -> None:
    html = (
        '<link rel="next" href="/blog/page/69/">'
        '<h2><a href="/blog/october-report/">October</a></h2>'
    )
    links, next_url = parse_archive(html)
    assert links == ["https://www.challengergray.com/blog/october-report/"]
    assert next_url is None


def test_parse_october_2025_separates_monthly_and_ytd() -> None:
    body = (
        "U.S.-based employers announced 153,074 job cuts in October, up 175% from last year. "
        "Through October, employers have announced 1,099,500 job cuts. "
        "WHICH INDUSTRIES ARE CUTTING? In October, the sector announced 33,281 job cuts. "
        "In October alone, Cost-Cutting was responsible for 50,437 announced layoffs. "
        "Artificial Intelligence (AI) was the second-most cited factor, leading to 31,039 job cuts "
        "as companies restructured. AI has been cited for 48,414 job cuts this year."
    )
    row = parse_report(
        _report_html(body, "2025-11-06"),
        "https://www.challengergray.com/blog/october-report/",
    )
    assert row is not None
    assert row["date"] == "2025-10-01"
    assert row["job_cuts_monthly"] == 153_074
    assert row["ai_job_cuts_monthly"] == 31_039
    assert row["job_cuts_ytd"] == 1_099_500
    assert row["ai_job_cuts_ytd"] == 48_414
    assert row["report_pdf_url"].endswith("Challenger-Report-October-2025.pdf")


def test_parse_december_uses_observation_year_and_both_ai_scopes() -> None:
    body = (
        "U.S.-based employers announced 34,817 cuts in December. "
        "In 2023, companies planned 721,677 job cuts. "
        "Companies cited Artificial Intelligence for 4,247 cuts in 2023, "
        "150 of which occurred in December."
    )
    row = parse_report(
        _report_html(body, "2024-01-04"),
        "https://www.challengergray.com/blog/december-2023-report/",
    )
    assert row is not None
    assert row["date"] == "2023-12-01"
    assert row["job_cuts_ytd"] == 721_677
    assert row["ai_job_cuts_monthly"] == 150
    assert row["ai_job_cuts_ytd"] == 4_247


def test_ytd_difference_supplements_only_missing_monthly_value() -> None:
    january = parse_report(
        _report_html(
            "U.S.-based employers announced 82,307 cuts in January. "
            "Artificial Intelligence was cited for 381 job cuts in January.",
            "2024-02-01",
        ),
        "https://www.challengergray.com/blog/january-2024-report/",
    )
    february = parse_report(
        _report_html(
            "U.S.-based employers announced 84,638 cuts in February. "
            "So far this year, companies have announced plans to cut 166,945 jobs. "
            "Companies have explicitly mentioned artificial intelligence as the reason for "
            "383 job cuts so far this year.",
            "2024-03-07",
        ),
        "https://www.challengergray.com/blog/february-2024-report/",
    )
    frame = _make_frame([february, january])
    assert frame.loc["2024-01-01", "ai_job_cuts_ytd"] == 381
    assert frame.loc["2024-02-01", "ai_job_cuts_monthly"] == 2
    assert frame.loc["2024-02-01", "ai_monthly_basis"] == "ytd_difference"
    assert frame.loc["2024-02-01", "ai_job_cuts_ytd"] == 383


def test_ai_running_total_in_combined_reason_sentence() -> None:
    body = (
        "U.S.-based employers announced 72,821 cuts in September. "
        "So far this year, companies have planned 609,242 cuts. "
        "Last month, 15,750 job cuts were attributed to Cost-Cutting, while 14,054 were due "
        "to Closing. Artificial Intelligence (AI) was cited for 5,616 job cuts last month, "
        "all of which occurred in Technology, for a total of 12,742 this year."
    )
    row = parse_report(
        _report_html(body, "2024-10-03"),
        "https://www.challengergray.com/blog/september-2024-report/",
    )
    assert row is not None
    assert row["job_cuts_ytd"] == 609_242
    assert row["ai_job_cuts_monthly"] == 5_616
    assert row["ai_job_cuts_ytd"] == 12_742


def test_each_count_uses_its_nearest_month_in_mixed_report_sentence() -> None:
    body = (
        "U.S.-based employers announced 60,620 job cuts in March. "
        "In March, Artificial Intelligence (AI) led all reasons for job cuts, with 15,341 "
        "announced during the month; in February, it was cited for 4,680 job cuts, "
        "approximately 10% of that month's total. "
        "AI ranks fifth year-to-date with 27,645 cuts."
    )
    row = parse_report(
        _report_html(body, "2026-04-02"),
        "https://www.challengergray.com/blog/march-2026-report/",
    )
    assert row is not None
    assert row["ai_job_cuts_monthly"] == 15_341
    assert row["ai_job_cuts_ytd"] == 27_645


def test_approximate_and_since_inception_counts_are_not_exact_monthly_or_ytd() -> None:
    body = (
        "U.S.-based employers announced 62,075 job cuts in July. "
        "AI was cited for over 10,000 cuts last month. "
        "Since 2023, when this reason was first tracked, AI has been cited in "
        "79,449 job cut announcements."
    )
    row = parse_report(
        _report_html(body, "2025-07-31"),
        "https://www.challengergray.com/blog/july-2025-report/",
    )
    assert row is not None
    assert row["ai_job_cuts_monthly"] is None
    assert row["ai_job_cuts_ytd"] is None


def test_technological_update_total_is_not_ai_ytd() -> None:
    body = (
        "U.S.-based employers announced 47,999 cuts in June. "
        "Employers have announced 300,000 cuts so far this year. "
        "Technological Updates, including changes related to automation and AI implementation, "
        "have led to 20,000 job cuts in 2025. Of these, 75 were explicitly attributed to "
        "Artificial Intelligence."
    )
    row = parse_report(
        _report_html(body, "2025-07-02"),
        "https://www.challengergray.com/blog/june-2025-report/",
    )
    assert row is not None
    assert row["ai_job_cuts_monthly"] is None
    assert row["ai_job_cuts_ytd"] == 75


def test_sector_count_mentioning_ai_is_not_ai_attribution() -> None:
    body = (
        "U.S.-based employers announced 108,435 job cuts in January. "
        "Chemical manufacturers announced 4,701 job cuts in January, primarily from one "
        "announcement which cited a shift to implementing artificial intelligence and automation. "
        "Artificial Intelligence (AI) was cited for 7,624 job cuts in January."
    )
    row = parse_report(
        _report_html(body, "2026-02-05"),
        "https://www.challengergray.com/blog/january-2026-report/",
    )
    assert row is not None
    assert row["ai_job_cuts_monthly"] == 7_624


def test_share_uses_matching_scope_and_media_appendix_is_excluded() -> None:
    october = parse_report(
        _report_html(
            "U.S.-based employers announced 153,074 job cuts in October. "
            "Through October, employers have announced 1,099,500 job cuts. "
            "Artificial Intelligence was cited for 31,039 job cuts in October. "
            "AI has been cited for 48,414 job cuts this year.",
            "2025-11-06",
        ),
        "https://www.challengergray.com/blog/october-report/",
    )
    frame = _make_frame([october])
    assert frame.loc["2025-10-01", "ai_share_monthly_pct"] == pytest.approx(
        round(100 * 31_039 / 153_074, 4)
    )
    assert frame.loc["2025-10-01", "ai_share_ytd_pct"] == pytest.approx(
        round(100 * 48_414 / 1_099_500, 4)
    )

    may = parse_report(
        _report_html(
            "U.S.-based employers announced 80,089 cuts in May. # # # "
            "In May, 3,900 people were laid off due to AI in a media appendix.",
            "2023-06-01",
        ),
        "https://www.challengergray.com/blog/may-2023-report/",
    )
    assert may is not None
    assert may["ai_job_cuts_monthly"] is None
