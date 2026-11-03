# Challenger 月度裁员公开报告抓取器。
#
# 数据源为 Challenger, Gray & Christmas 官方归档与报告网页，并保留报告 PDF 链接。
# date 是观测月首日；job_cuts_monthly / ai_job_cuts_monthly / ai_share_monthly_pct
# 只表示当月，job_cuts_ytd / ai_job_cuts_ytd / ai_share_ytd_pct 只表示年初至观测月。
# 占比由同口径人数计算。网页未披露精确 AI 人数时保留缺失，不把累计自 2023 年、
# 约数或 Technological Update 当作当月 AI 人数。相邻月份都有 YTD 时，可用差值补充
# 当月人数，并在 ai_monthly_basis 标记 ytd_difference。
#
# 用法：
#   uv run python -m src.fetchers.challenger_fetcher
#   uv run python -m src.fetchers.challenger_fetcher --output .scratch/challenger.csv

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://www.challengergray.com/blog/category/job-cuts-report/"
OUT = ROOT / "data" / "challenger" / "challenger.csv"
CACHE_DIR = ROOT / ".scratch" / "challenger" / "pages"
START_MONTH = "2023-05"  # 官方从 2023 年 5 月开始单独追踪 AI 归因裁员
COLUMNS = [
    "job_cuts_monthly",
    "ai_job_cuts_monthly",
    "ai_share_monthly_pct",
    "job_cuts_ytd",
    "ai_job_cuts_ytd",
    "ai_share_ytd_pct",
    "ai_monthly_basis",
    "published_at",
    "source_url",
    "report_pdf_url",
]
MONTHS = {
    name: i
    for i, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}
MONTH_RE = "|".join(MONTHS)
AI_RE = re.compile(r"Artificial Intelligence|\bAI\b", re.IGNORECASE)
TOTAL_RE = re.compile(
    rf"announced(?:\s+plans\s+to\s+cut)?\s+([\d,]+)\s+"
    rf"(?:job\s+cuts?|cuts?|jobs?)\s+in\s+({MONTH_RE})\b",
    re.IGNORECASE,
)
COUNT_RE = re.compile(
    r"(?<![\d,])([\d,]+)\s+(?:announced\s+)?"
    r"(?:job[\s-]+cuts?(?:\s+announcements?)?|cuts?|layoffs?|"
    r"job[\s-]+reductions?|announced\s+layoff\s+plans)\b",
    re.IGNORECASE,
)


class _ArchiveParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_h2 = False
        self.links: list[str] = []
        self.next_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h2":
            self.in_h2 = True
        elif tag == "a" and self.in_h2 and values.get("href"):
            self.links.append(values["href"] or "")
        elif (
            tag == "link"
            and values.get("rel") == "next"
            and values.get("href")
            and self.next_url is None
        ):
            self.next_url = values["href"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self.in_h2 = False


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_article = False
        self.skip_tag: str | None = None
        self.parts: list[str] = []
        self.published_at = ""
        self.pdf_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "article:published_time":
            self.published_at = values.get("content") or ""
        if tag == "article":
            self.in_article = True
        if not self.in_article:
            return
        if tag in {"script", "style", "noscript"}:
            self.skip_tag = tag
        if tag == "a" and not self.pdf_url:
            href = values.get("href") or ""
            if ".pdf" in urlparse(href).path.lower():
                self.pdf_url = href

    def handle_endtag(self, tag: str) -> None:
        if tag == self.skip_tag:
            self.skip_tag = None
        if tag == "article":
            self.in_article = False

    def handle_data(self, data: str) -> None:
        if self.in_article and self.skip_tag is None:
            self.parts.append(data)


def _official_url(href: str, base_url: str) -> str:
    url = urldefrag(urljoin(base_url, href))[0]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "challengergray.com",
        "www.challengergray.com",
    }:
        return ""
    return url


def parse_archive(html: str, base_url: str = ARCHIVE_URL) -> tuple[list[str], str | None]:
    """提取归档中的报告链接与下一页链接。"""
    parser = _ArchiveParser()
    parser.feed(html)
    links = list(dict.fromkeys(filter(None, (_official_url(v, base_url) for v in parser.links))))
    next_url = _official_url(parser.next_url, base_url) if parser.next_url else None
    # 分类末页的 rel=next 会进入全站博客分页；越出分类即停止，避免抓取数百篇无关文章。
    if next_url and not next_url.startswith(
        "https://www.challengergray.com/blog/category/job-cuts-report/page/"
    ):
        next_url = None
    return links, next_url


def _number(value: str) -> int:
    return int(value.replace(",", ""))


def _total_ytd(text: str, month: str, year: int) -> int | None:
    header = re.split(
        r"(?:WHICH\s+)?INDUSTRIES\s+(?:ARE\s+)?CUTTING",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    count = r"([\d,]+)\s+(?:job\s+cuts?|cuts?|jobs?)"
    patterns = [
        rf"Through\s+{month},[^.]*?announced\s+{count}",
        rf"So\s+far\s+this\s+year,[^.]*?(?:announced|planned)(?:\s+plans\s+to\s+cut)?\s+{count}",
        rf"So\s+far\s+in\s+{year},[^.]*?(?:announced|planned)(?:\s+plans\s+to\s+cut)?\s+{count}",
        rf"announced\s+{count}\s+so\s+far\s+this\s+year",
        rf"In\s+{year},[^.]*?(?:planned|announced)(?:\s+plans\s+to\s+cut)?\s+{count}",
        rf"For\s+the\s+year,[^.]*?announced\s+{count}",
    ]
    # 总裁员 YTD 只在开篇判断，避免把后文 News 等行业 YTD 当成全市场总数。
    header = " ".join(re.split(r"(?<=[.!?])\s+", header)[:6])
    for pattern in patterns:
        match = re.search(pattern, header, re.IGNORECASE)
        if match:
            return _number(match.group(1))
    return None


def _historical_reference(before: str, month: str, year: int) -> bool:
    references = list(
        re.finditer(rf"({MONTH_RE})(?:\s+of)?\s+(20\d{{2}})", before[-140:], re.IGNORECASE)
    )
    if not references:
        return False
    latest = references[-1]
    return not (latest.group(1).lower() == month.lower() and int(latest.group(2)) == year)


def _count_scope(sentence: str, start: int, end: int, month: str, year: int) -> str:
    before = sentence[:start]
    after = sentence[end:]
    if _historical_reference(before, month, year) or re.search(
        r"(?:over|nearly|almost|approximately|about)\s+$", before, re.IGNORECASE
    ):
        return "skip"
    month_mentions = list(re.finditer(rf"\b({MONTH_RE})\b", sentence))
    if month_mentions:
        nearest = min(
            month_mentions,
            key=lambda match: (
                (
                    start - match.end()
                    if match.end() <= start
                    else match.start() - end
                    if match.start() >= end
                    else 0
                ),
                0 if match.start() >= end else 1,
            ),
        )
        if nearest.group(1).lower() != month.lower():
            return "skip"
    ytd = (
        rf"this\s+year|for\s+the\s+year|so\s+far\s+in\s+{year}|in\s+{year}|"
        rf"year[\s-]+to[\s-]+date|\bYTD\b|through\s+{month}"
    )
    if re.search(ytd, before[-240:], re.IGNORECASE) or re.search(
        rf"{ytd}|in\s+{year}\b", after[:70], re.IGNORECASE
    ):
        return "ytd"
    monthly = rf"in\s+{month}\b|during\s+{month}\b|last\s+month|during\s+the\s+month|monthly"
    if re.search(monthly, before[-240:], re.IGNORECASE) or re.search(
        monthly, after[:70], re.IGNORECASE
    ):
        return "monthly"
    return ""


def _extract_ai(text: str, month: str, year: int) -> tuple[int | None, int | None]:
    monthly: list[int] = []
    ytd: list[int] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not AI_RE.search(sentence) or re.search(
            r"Technological\s+Updates?", sentence, re.IGNORECASE
        ):
            continue
        for match in COUNT_RE.finditer(sentence):
            local_before = sentence[max(0, match.start() - 180) : match.start()]
            if re.fullmatch(r"(?:19|20)\d{2}", match.group(1)) and re.search(
                r"\b(?:all|in|of)\s+$", local_before, re.IGNORECASE
            ):
                continue
            local_after = sentence[match.end() : match.end() + 45]
            if not AI_RE.search(local_before) and not re.search(
                r"(?:due|attributed|because)\s+(?:explicitly\s+)?(?:to\s+)?"
                r"(?:Artificial\s+Intelligence|AI)\b",
                local_after,
                re.IGNORECASE,
            ):
                continue
            scope = _count_scope(sentence, match.start(), match.end(), month, year)
            value = _number(match.group(1))
            if not scope and not re.match(r"(?:Of\s+these|Another)\b", sentence, re.IGNORECASE):
                other_year = rf"\bin\s+(?!{year}\b)20\d{{2}}\b"
                excluded = rf"\bSince\b|last\s+year|previous\s+year|{other_year}"
                if not re.search(excluded, sentence, re.IGNORECASE):
                    scope = "monthly"
            if scope == "monthly":
                monthly.append(value)
            elif scope == "ytd":
                ytd.append(value)
        match = re.search(
            rf"([\d,]+)\s+of\s+which\s+occurred\s+in\s+{month}\b", sentence, re.IGNORECASE
        )
        if match:
            monthly.append(_number(match.group(1)))
        match = re.search(
            r"(?:bringing\s+the\s+total\s+to|for\s+a\s+total\s+of)\s+([\d,]+)\b",
            sentence,
            re.IGNORECASE,
        )
        if match:
            ytd.append(_number(match.group(1)))
        match = re.search(r"([\d,]+)\s+announced\s+during\s+the\s+month\b", sentence, re.IGNORECASE)
        if match:
            monthly.append(_number(match.group(1)))

    # 2025 年部分报告把 AI YTD 写成 Technological Update 总数之后的 Of these / Another。
    match = re.search(
        rf"Technological\s+Updates?[^.]*\bin\s+{year}\b[^.]*\.\s*"
        rf"(?:Of\s+these|Another),?\s+([\d,]+)\s+(?:were\s+)?explicitly\s+attributed\s+to\s+"
        rf"(?:Artificial\s+Intelligence|AI)\b",
        text,
        re.IGNORECASE,
    )
    if match:
        ytd.append(_number(match.group(1)))

    def unique(values: list[int]) -> int | None:
        return values[0] if values and len(set(values)) == 1 else None

    return unique(monthly), unique(ytd)


def parse_report(html: str, url: str) -> dict | None:
    """解析一篇官方报告；非月度报告或缺少观测月时返回 None。"""
    parser = _ReportParser()
    parser.feed(html)
    published = re.match(r"(20\d{2})-(\d{2})-(\d{2})", parser.published_at)
    if not published:
        return None
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip().split("# # #", 1)[0]
    total = TOTAL_RE.search(text)
    if not total:
        return None
    month = next(name for name in MONTHS if name.lower() == total.group(2).lower())
    published_year, published_month = int(published.group(1)), int(published.group(2))
    year = published_year - 1 if MONTHS[month] == 12 and published_month == 1 else published_year
    content = text[total.start() :]
    ai_monthly, ai_ytd = _extract_ai(text[total.end() :], month, year)
    return {
        "date": f"{year}-{MONTHS[month]:02d}-01",
        "job_cuts_monthly": _number(total.group(1)),
        "ai_job_cuts_monthly": ai_monthly,
        "job_cuts_ytd": _total_ytd(content, month, year),
        "ai_job_cuts_ytd": ai_ytd,
        "ai_monthly_basis": "reported" if ai_monthly is not None else "unreported",
        "published_at": published.group(0),
        "source_url": url,
        "report_pdf_url": _official_url(parser.pdf_url, url) if parser.pdf_url else "",
    }


def _month_shift(date: str, offset: int) -> str:
    year, month = int(date[:4]), int(date[5:7]) + offset
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return f"{year}-{month:02d}-01"


def _make_frame(records: list[dict]) -> pd.DataFrame:
    rows = {row["date"]: dict(row) for row in records}
    for date, row in sorted(rows.items()):
        if date[5:7] == "01":
            if row["job_cuts_ytd"] is None:
                row["job_cuts_ytd"] = row["job_cuts_monthly"]
            if row["ai_job_cuts_ytd"] is None and row["ai_job_cuts_monthly"] is not None:
                row["ai_job_cuts_ytd"] = row["ai_job_cuts_monthly"]
            if row["ai_job_cuts_monthly"] is None and row["ai_job_cuts_ytd"] is not None:
                row["ai_job_cuts_monthly"] = row["ai_job_cuts_ytd"]
                row["ai_monthly_basis"] = "january_ytd"
        previous = rows.get(_month_shift(date, -1))
        if (
            row["ai_job_cuts_monthly"] is None
            and row["ai_job_cuts_ytd"] is not None
            and previous is not None
            and previous["ai_job_cuts_ytd"] is not None
        ):
            difference = row["ai_job_cuts_ytd"] - previous["ai_job_cuts_ytd"]
            if difference >= 0:
                row["ai_job_cuts_monthly"] = difference
                row["ai_monthly_basis"] = "ytd_difference"

    for row in rows.values():
        monthly, ai_monthly = row["job_cuts_monthly"], row["ai_job_cuts_monthly"]
        ytd, ai_ytd = row["job_cuts_ytd"], row["ai_job_cuts_ytd"]
        row["ai_share_monthly_pct"] = (
            round(100 * ai_monthly / monthly, 4) if ai_monthly is not None and monthly else None
        )
        row["ai_share_ytd_pct"] = (
            round(100 * ai_ytd / ytd, 4) if ai_ytd is not None and ytd else None
        )
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame([rows[date] for date in sorted(rows)]).set_index("date")[COLUMNS]


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; market-base-challenger/1.0)"
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _get(url: str, cache_dir: Path | None) -> str:
    cache_file = (
        cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.html"
        if cache_dir is not None
        else None
    )
    if cache_file is not None and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    response = _new_session().get(url, timeout=45)
    response.raise_for_status()
    text = response.text
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")
    return text


def fetch_challenger(
    start_month: str = START_MONTH,
    cache_dir: Path | None = CACHE_DIR,
    max_workers: int = 4,
) -> pd.DataFrame:
    """抓取官方归档中 start_month 起的全部月度报告，HTML 可缓存复用。"""
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", start_month):
        raise ValueError(f"start_month 必须为 YYYY-MM: {start_month!r}")
    if max_workers < 1:
        raise ValueError("max_workers 必须为正数")
    report_urls: list[str] = []
    archive_url: str | None = ARCHIVE_URL
    seen_archives: set[str] = set()
    while archive_url:
        if archive_url in seen_archives:
            raise RuntimeError(f"归档分页循环: {archive_url}")
        seen_archives.add(archive_url)
        links, next_url = parse_archive(_get(archive_url, cache_dir), archive_url)
        report_urls.extend(url for url in links if url not in report_urls)
        archive_url = next_url
        time.sleep(0.1)

    logger.info("Challenger 归档发现 %d 篇候选报告", len(report_urls))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pages = list(pool.map(lambda url: _get(url, cache_dir), report_urls))
    records = []
    for index, (url, html) in enumerate(zip(report_urls, pages, strict=True), 1):
        record = parse_report(html, url)
        if record and record["date"] >= f"{start_month}-01":
            records.append(record)
        if index % 10 == 0:
            logger.info("Challenger 已处理 %d/%d 篇", index, len(report_urls))
    frame = _make_frame(records)
    if frame.empty:
        raise RuntimeError(f"Challenger 未解析到 {start_month} 起的月度报告")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Challenger 月度裁员与 AI 归因裁员")
    parser.add_argument("--output", type=Path, default=OUT, help="CSV 输出路径")
    parser.add_argument("--start-month", default=START_MONTH, help="最早观测月，YYYY-MM")
    parser.add_argument("--backfill", action="store_true", help="覆盖输出文件")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    frame = fetch_challenger(args.start_month, cache_dir=args.cache_dir, max_workers=args.workers)
    upsert_timeseries(args.output, frame, backfill=args.backfill, column_order=COLUMNS)
    logger.info(
        "Challenger → %s：%d 个月（%s 至 %s），当月 AI 精确值 %d 个月",
        args.output,
        len(frame),
        frame.index[0],
        frame.index[-1],
        int(frame["ai_job_cuts_monthly"].notna().sum()),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
