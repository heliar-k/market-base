import argparse
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from ..config import ROOT

INDEX_URL = "https://www.stats.gov.cn/english/PressRelease/"
OUTPUT_PATH = ROOT / "data" / "china" / "nbs_monthly.csv"
CACHE_PATH = ROOT / ".scratch" / "nbs_monthly" / "pages"

RETAIL = "retail"
REAL_ESTATE = "real_estate"
CPI = "cpi"
PPI = "ppi"
TITLE_PREFIXES = {
    RETAIL: ("Total Retail Sales of Consumer Goods",),
    REAL_ESTATE: ("Investment in Real Estate Development",),
    CPI: ("Consumer Price Index",),
    PPI: (
        "Industrial Producer Price Indexes",
        "Producer Price Index in the Industrial Sector",
    ),
}
OUTPUT_COLUMNS = [
    "nbs_retail_sales_yoy_pct",
    "nbs_retail_sales_ytd_yoy_pct",
    "nbs_cpi_yoy_pct",
    "nbs_cpi_ytd_yoy_pct",
    "nbs_ppi_yoy_pct",
    "nbs_ppi_ytd_yoy_pct",
    "nbs_real_estate_investment_ytd_yoy_pct",
    "nbs_new_commercial_floor_space_sold_ytd_yoy_pct",
]
MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\b", re.IGNORECASE)
NUMBER_RE = re.compile(r"^[+−–-]?\d[\d,]*(?:\.\d+)?$")


@dataclass(frozen=True)
class ReleaseRef:
    kind: str
    title: str
    url: str


class _IndexHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.releases: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        title = (attributes.get("title") or "").strip()
        href = attributes.get("href")
        if not href:
            return
        for kind, prefixes in TITLE_PREFIXES.items():
            if title.startswith(prefixes):
                self.releases.append((kind, title, href))
                break


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def parse_index(html: str, base_url: str = INDEX_URL) -> list[ReleaseRef]:
    parser = _IndexHTMLParser()
    parser.feed(html)
    return [
        ReleaseRef(kind, title, urljoin(base_url, href)) for kind, title, href in parser.releases
    ]


def parse_page_count(html: str) -> int:
    match = re.search(r"\bvar\s+countPage\s*=\s*(\d+)", html)
    if match is None:
        raise ValueError("NBS 英文发布索引缺少分页数")
    count = int(match.group(1))
    if count < 1:
        raise ValueError(f"NBS 英文发布索引分页数无效: {count}")
    return count


def observation_end(title: str) -> pd.Timestamp:
    """从英文标题的报告期提取期末；全年与半年标题分别取 12 月和 6 月。"""
    clean_title = _clean_text(title)
    year_match = re.search(r"\b(20\d{2})\b", clean_title)
    if year_match is None:
        raise ValueError(f"发布稿标题缺少年份: {title}")
    year = int(year_match.group(1))
    before_year = clean_title[: year_match.start()]
    lowered = before_year.lower()
    if "first three quarters" in lowered:
        month = 9
    elif "first half" in lowered:
        month = 6
    else:
        month_matches = list(MONTH_RE.finditer(before_year))
        month = MONTHS[month_matches[-1].group(1).lower()] if month_matches else 12
    return pd.Period(f"{year}-{month:02d}", freq="M").end_time.normalize()


def _table_rows(html: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    return parser.rows


def _numbers(row: list[str]) -> list[float]:
    numbers = []
    for cell in row[1:]:
        compact = cell.replace(" ", "")
        if NUMBER_RE.fullmatch(compact):
            numbers.append(float(compact.replace(",", "").replace("−", "-").replace("–", "-")))
    return numbers


def _matching_numbers(rows: list[list[str]], label_pattern: str) -> list[float]:
    pattern = re.compile(label_pattern, re.IGNORECASE)
    matches = [_numbers(row) for row in rows if row and pattern.fullmatch(row[0])]
    matches = [numbers for numbers in matches if numbers]
    if len(matches) != 1:
        raise ValueError(f"发布稿指标行数量异常: {label_pattern}, matches={len(matches)}")
    return matches[0]


def _retail_values(rows: list[list[str]]) -> dict[str, float]:
    numbers = _matching_numbers(rows, r"total retail sales of (?:social )?consumer goods")
    if len(numbers) == 4:
        return {
            "nbs_retail_sales_yoy_pct": numbers[1],
            "nbs_retail_sales_ytd_yoy_pct": numbers[3],
        }
    if len(numbers) == 2:
        # 1—2 月合并发布稿只有累计列，不能将合并期读数标成 2 月单月读数。
        return {"nbs_retail_sales_ytd_yoy_pct": numbers[1]}
    raise ValueError(f"社会消费品零售总额表格列数异常: {numbers}")


def _price_values(rows: list[list[str]], kind: str, month: int) -> dict[str, float]:
    if kind == CPI:
        label = r"consumer price index"
        prefix = "nbs_cpi"
    else:
        label = r"(?:i\. )?producer (?:price indexes?|prices) for industrial products"
        prefix = "nbs_ppi"
    numbers = _matching_numbers(rows, label)
    if len(numbers) == 2 and month == 1:
        return {f"{prefix}_yoy_pct": numbers[1], f"{prefix}_ytd_yoy_pct": numbers[1]}
    if len(numbers) != 3:
        raise ValueError(f"{kind.upper()} 表格列数异常: {numbers}")
    return {f"{prefix}_yoy_pct": numbers[1], f"{prefix}_ytd_yoy_pct": numbers[2]}


def _real_estate_values(rows: list[list[str]]) -> dict[str, float]:
    investment = _matching_numbers(
        rows, r"investment in real estate development \(100 million yuan\)"
    )
    floor_space = _matching_numbers(
        rows, r"floor space of (?:newly built )?commercial buildings sold \(10,000 sq\.m\)"
    )
    if len(investment) != 2 or len(floor_space) != 2:
        raise ValueError(f"房地产表格列数异常: investment={investment}, floor_space={floor_space}")
    return {
        "nbs_real_estate_investment_ytd_yoy_pct": investment[1],
        "nbs_new_commercial_floor_space_sold_ytd_yoy_pct": floor_space[1],
    }


def parse_release(ref: ReleaseRef, html: str) -> dict[str, object]:
    """读取发布稿直接披露的同比单元格，不从绝对值推算增速。"""
    rows = _table_rows(html)
    date = observation_end(ref.title)
    if ref.kind == RETAIL:
        values = _retail_values(rows)
    elif ref.kind in {CPI, PPI}:
        values = _price_values(rows, ref.kind, date.month)
    elif ref.kind == REAL_ESTATE:
        values = _real_estate_values(rows)
    else:
        raise ValueError(f"未知 NBS 发布稿类型: {ref.kind}")
    return {"date": date, **values}


def releases_to_frame(records: list[dict[str, object]]) -> pd.DataFrame:
    if not records:
        raise ValueError("NBS 发布稿没有产生任何观测")
    by_date: dict[pd.Timestamp, dict[str, object]] = {}
    for record in records:
        date = pd.Timestamp(record["date"])
        row = by_date.setdefault(date, {})
        for column, value in record.items():
            if column == "date":
                continue
            if column in row and pd.notna(row[column]) and row[column] != value:
                raise ValueError(f"NBS 指标在 {date.date()} 出现冲突: {column}")
            row[column] = value
    df = pd.DataFrame.from_dict(by_date, orient="index").sort_index()
    df.index = pd.DatetimeIndex(df.index, name="date")
    return df.reindex(columns=OUTPUT_COLUMNS)


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
    )
    return session


def is_access_challenge(html: str) -> bool:
    return "Please enable JavaScript" in html and "WZWS" in html


def fetch_nbs_monthly(cache_dir: Path | None = None, delay: float = 1.0) -> pd.DataFrame:
    """遍历 NBS 英文发布索引全部归档，返回发布稿直接披露的月度与累计同比。"""
    session = _new_session()
    last_started = 0.0

    def get(url: str, cache: bool = False, max_age: float | None = None) -> str:
        nonlocal last_started
        cache_file = cache_dir / Path(urlparse(url).path).name if cache_dir and cache else None
        if cache_file is not None and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if max_age is None or age <= max_age:
                return cache_file.read_text(encoding="utf-8")
        wait = delay - (time.monotonic() - last_started)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            last_started = time.monotonic()
            try:
                response = session.get(url, timeout=45)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            text = response.content.decode("utf-8")
            if is_access_challenge(text):
                if attempt < 2:
                    time.sleep(5 * 2**attempt)
                    continue
                raise RuntimeError(f"NBS 英文页面返回 JavaScript 访问验证: {url}")
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(text, encoding="utf-8")
            return text
        raise RuntimeError("NBS 请求重试结束后仍未获得响应")

    first_html = get(INDEX_URL, cache=True, max_age=3600)
    page_count = parse_page_count(first_html)
    refs = parse_index(first_html)
    for page in range(1, page_count):
        url = urljoin(INDEX_URL, f"index_{page}.html")
        refs.extend(parse_index(get(url, cache=True, max_age=3600), base_url=url))
    unique_refs = list({ref.url: ref for ref in refs}.values())
    if not unique_refs:
        raise ValueError("NBS 英文发布索引没有找到目标发布稿")
    records = [parse_release(ref, get(ref.url, cache=True)) for ref in unique_refs]
    return releases_to_frame(records)


def main() -> None:
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="国家统计局英文发布稿月度与累计同比")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_PATH)
    parser.add_argument("--delay", type=float, default=1.0, help="请求启动时刻的最小间隔秒数")
    args = parser.parse_args()

    df = fetch_nbs_monthly(cache_dir=args.cache_dir, delay=args.delay)
    upsert_timeseries(args.output, df, column_order=OUTPUT_COLUMNS)
    print(f"NBS 月度发布稿 → {args.output}（{len(df)} 个观测期末）")
    for column in OUTPUT_COLUMNS:
        series = df[column].dropna()
        print(
            f"{column}: {series.index[0].date()} → {series.index[-1].date()}，"
            f"{len(series)} 个观测，最新 {series.iloc[-1]}%"
        )


if __name__ == "__main__":
    main()
