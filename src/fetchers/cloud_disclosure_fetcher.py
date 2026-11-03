from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..config import ROOT
from . import sec_fetcher
from ._io import upsert_rows

logger = logging.getLogger(__name__)

SCRATCH_DIR = ROOT / ".scratch" / "cloud_disclosure"
OUTPUT_DIR = ROOT / "data" / "ai_cycle"
SEC_DIR = ROOT / "data" / "sec"
CIKS = {
    "MSFT": "789019",
    "GOOG": "1652044",
    "AMZN": "1018724",
    "META": "1326801",
}
REVENUE_CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
REVENUE_CONCEPTS = {REVENUE_CONCEPT, "Revenues"}
OUTPUT_COLUMNS = [
    "symbol",
    "cloud_unit",
    "availability",
    "form",
    "filing_date",
    "accession",
    "fiscal_period",
    "period_type",
    "period_start",
    "period_end",
    "revenue_usd",
    "prior_year_revenue_usd",
    "revenue_growth_pct",
    "growth_basis",
    "growth_source",
    "xbrl_concept",
    "xbrl_member",
    "filing_url",
    "xbrl_url",
    "missing_reason",
]
CAPEX_COLUMNS = [
    "symbol",
    "announced_on",
    "guidance_period",
    "minimum_usd",
    "maximum_usd",
    "point_estimate_usd",
    "currency",
    "source_url",
    "source_excerpt",
    "extraction_method",
]


@dataclass(frozen=True)
class _CloudSpec:
    cloud_unit: str
    axis: str
    member: str


@dataclass(frozen=True)
class _Context:
    start: date | None
    end: date | None
    dimensions: dict[str, str]


@dataclass(frozen=True)
class _Fact:
    concept: str
    context: _Context
    value: Decimal


_CLOUD_SPECS = {
    "AMZN": (
        _CloudSpec("AWS", "StatementBusinessSegmentsAxis", "amzn:AmazonWebServicesSegmentMember"),
        _CloudSpec("AWS", "ProductOrServiceAxis", "amzn:AmazonWebServicesMember"),
    ),
    "GOOG": (
        _CloudSpec("Google Cloud", "StatementBusinessSegmentsAxis", "goog:GoogleCloudMember"),
    ),
    "MSFT": (_CloudSpec("Azure", "ProductOrServiceAxis", "msft:AzureMember"),),
    "META": (),
}

_AZURE_GROWTH_PATTERNS = (
    (
        "Azure and other cloud services",
        re.compile(
            r"Azure and other cloud services revenue (?:grew|increased) "
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    ),
    (
        "Azure",
        re.compile(r"Azure revenue (?:grew|increased) (?P<value>\d+(?:\.\d+)?)%"),
    ),
    (
        "Azure",
        re.compile(
            r"Azure revenue (?:grew|increased) \$[\d.]+ billion or "
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    ),
)


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _member_local_name(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _parse_contexts(root: ET.Element) -> dict[str, _Context]:
    contexts = {}
    for element in root.iter():
        if _local_name(element.tag) != "context":
            continue
        start = None
        end = None
        dimensions = {}
        for node in element.iter():
            kind = _local_name(node.tag)
            if kind == "startDate":
                start = date.fromisoformat(node.text or "")
            elif kind in ("endDate", "instant"):
                end = date.fromisoformat(node.text or "")
            elif kind == "explicitMember":
                dimensions[_member_local_name(node.attrib["dimension"])] = (node.text or "").strip()
        contexts[element.attrib["id"]] = _Context(start, end, dimensions)
    return contexts


def _document_value(root: ET.Element, name: str) -> str:
    values = [
        (element.text or "").strip()
        for element in root
        if _local_name(element.tag) == name and (element.text or "").strip()
    ]
    if len(set(values)) != 1:
        raise ValueError(f"XBRL 缺少唯一的 {name}: {values}")
    return values[0]


def _revenue_facts(root: ET.Element, contexts: dict[str, _Context]) -> list[_Fact]:
    facts = []
    for element in root:
        concept = _local_name(element.tag)
        context_ref = element.attrib.get("contextRef")
        unit = element.attrib.get("unitRef", "").upper()
        if concept not in REVENUE_CONCEPTS or context_ref not in contexts or "USD" not in unit:
            continue
        context = contexts[context_ref]
        if context.start is None or context.end is None:
            continue
        facts.append(_Fact(concept, context, Decimal((element.text or "").strip())))
    return facts


def _matches_spec(fact: _Fact, spec: _CloudSpec) -> bool:
    dimensions = fact.context.dimensions
    return (
        len(dimensions) == 1
        and dimensions.get(spec.axis) is not None
        and _member_local_name(dimensions[spec.axis]) == _member_local_name(spec.member)
    )


def _select_cloud_facts(symbol: str, facts: list[_Fact]) -> tuple[_CloudSpec | None, list[_Fact]]:
    for spec in _CLOUD_SPECS[symbol]:
        selected = [fact for fact in facts if _matches_spec(fact, spec)]
        if selected:
            return spec, selected
    return None, []


def _period_type(form: str, start: date, end: date) -> str:
    days = (end - start).days
    if form == "10-K" and days > 300:
        return "fy"
    if 75 <= days <= 100:
        return "quarter"
    return "ytd"


def _previous_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _fact_map(facts: list[_Fact]) -> dict[tuple[date, date], _Fact]:
    result = {}
    for fact in facts:
        assert fact.context.start is not None
        key = (fact.context.start, fact.context.end)
        previous = result.get(key)
        if previous is not None and previous.value != fact.value:
            raise ValueError(f"同一云收入期间出现不同 XBRL 数值: {key}")
        result[key] = fact
    return result


def _azure_growths(text: str) -> list[tuple[int, float, str]]:
    normalized = re.sub(r"\s+", " ", text)
    matches = []
    for scope, pattern in _AZURE_GROWTH_PATTERNS:
        for match in pattern.finditer(normalized):
            matches.append((match.start(), float(match.group("value")), scope))
    return sorted(matches)


def _growth_for_period(
    growths: list[tuple[int, float, str]], period_type: str
) -> tuple[float | None, str]:
    if not growths:
        return None, ""
    # 10-Q 的概览与单季段在前，YTD 段在后；10-K 只有一个年度期间。
    selected = growths[-1] if period_type in ("ytd", "fy") else growths[0]
    return selected[1], selected[2]


def _missing_reason(symbol: str, has_absolute: bool, has_growth: bool) -> str:
    if symbol == "META":
        return "No separately reported cloud segment or cloud revenue metric in this filing"
    reasons = []
    if not has_absolute:
        reasons.append("Cloud absolute revenue is not separately disclosed for this period")
    if not has_growth:
        reasons.append("Year-over-year cloud revenue growth is unavailable for this period")
    return "; ".join(reasons)


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def parse_filing_disclosures(
    symbol: str,
    xbrl_content: bytes | str,
    filing_text: str,
    *,
    form: str,
    filing_date: str,
    accession: str,
    filing_url: str,
    xbrl_url: str,
) -> pd.DataFrame:
    """解析一份 SEC 申报，保留当期单季、YTD/FY 与上年同期的口径。"""
    symbol = symbol.upper()
    if symbol not in CIKS:
        raise ValueError(f"不支持的云厂商: {symbol}")
    root = ET.fromstring(xbrl_content)
    contexts = _parse_contexts(root)
    document_end = date.fromisoformat(_document_value(root, "DocumentPeriodEndDate"))
    fiscal_period = _document_value(root, "DocumentFiscalPeriodFocus")
    facts = _revenue_facts(root, contexts)
    spec, cloud_facts = _select_cloud_facts(symbol, facts)
    cloud_by_period = _fact_map(cloud_facts)

    current_periods = {
        (fact.context.start, fact.context.end)
        for fact in facts
        if fact.context.end == document_end
        and fact.context.start is not None
        and (
            not fact.context.dimensions or any(_matches_spec(fact, s) for s in _CLOUD_SPECS[symbol])
        )
    }
    if not current_periods:
        raise ValueError(f"{symbol} {accession} 没有当期收入期间")

    azure_growths = _azure_growths(filing_text) if symbol == "MSFT" else []
    rows = []
    for start, end in sorted(current_periods, key=lambda item: ((item[1] - item[0]).days, item[0])):
        assert start is not None and end is not None
        period_type = _period_type(form, start, end)
        fact = cloud_by_period.get((start, end))
        prior = cloud_by_period.get((_previous_year(start), _previous_year(end)))
        growth = None
        growth_source = ""
        cloud_unit = spec.cloud_unit if spec is not None else ""
        if symbol == "MSFT":
            growth, text_scope = _growth_for_period(azure_growths, period_type)
            if text_scope:
                cloud_unit = text_scope
            if growth is not None:
                growth_source = "sec_filing_text"
        if growth is None and fact is not None and prior is not None and prior.value != 0:
            growth = float((fact.value / prior.value - 1) * 100)
            growth_source = "calculated_from_xbrl"
        has_absolute = fact is not None
        has_growth = growth is not None
        if has_absolute and has_growth:
            availability = "absolute_and_growth"
        elif has_absolute:
            availability = "absolute_only"
        elif has_growth:
            availability = "growth_only"
        else:
            availability = "not_disclosed"
        rows.append(
            {
                "symbol": symbol,
                "cloud_unit": cloud_unit,
                "availability": availability,
                "form": form,
                "filing_date": filing_date,
                "accession": accession,
                "fiscal_period": fiscal_period,
                "period_type": period_type,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "revenue_usd": int(fact.value) if fact is not None else None,
                "prior_year_revenue_usd": int(prior.value) if prior is not None else None,
                "revenue_growth_pct": growth,
                "growth_basis": "yoy" if has_growth else "",
                "growth_source": growth_source,
                "xbrl_concept": f"us-gaap:{fact.concept}" if fact is not None else "",
                "xbrl_member": spec.member if fact is not None and spec is not None else "",
                "filing_url": filing_url,
                "xbrl_url": xbrl_url,
                "missing_reason": _missing_reason(symbol, has_absolute, has_growth),
            }
        )
    return _frame(rows, OUTPUT_COLUMNS)


def _filing_index(cik: str, years: int) -> list[dict]:
    raw = json.loads(sec_fetcher._get(f"{sec_fetcher.DATA_BASE}/submissions/CIK{cik:0>10}.json"))
    recent = raw["filings"]["recent"]
    cutoff = date.today() - timedelta(days=int(years * 365.25))
    fields = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "form",
        "primaryDocument",
    )
    items = []
    for accession, filing_date, report_date, form, primary_doc in zip(
        *(recent[field] for field in fields), strict=True
    ):
        if form not in ("10-Q", "10-K") or date.fromisoformat(filing_date) < cutoff:
            continue
        if not primary_doc.lower().endswith((".htm", ".html")):
            continue
        directory = f"{sec_fetcher.BASE}/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
        filing_url = f"{directory}/{primary_doc}"
        items.append(
            {
                "accession": accession,
                "filing_date": filing_date,
                "report_date": report_date,
                "form": form,
                "filing_url": filing_url,
                "xbrl_url": f"{directory}/{Path(primary_doc).stem}_htm.xml",
            }
        )
    return sorted(items, key=lambda item: item["filing_date"], reverse=True)


def _read_filing_text(
    symbol: str,
    metadata: dict,
    *,
    sec_dir: Path,
    source_dir: Path,
) -> str:
    local_path = sec_dir / symbol / f"{metadata['form']}_{metadata['filing_date']}.txt.gz"
    if local_path.exists():
        with gzip.open(local_path, "rt", encoding="utf-8") as file:
            return file.read()
    cached_path = source_dir / f"{symbol}_{metadata['form']}_{metadata['filing_date']}.txt"
    if cached_path.exists():
        return cached_path.read_text(encoding="utf-8")
    raw = sec_fetcher._get(metadata["filing_url"])
    text = sec_fetcher._extract_text(raw.decode("utf-8", errors="replace"))
    cached_path.write_text(text, encoding="utf-8")
    return text


def _read_xbrl(metadata: dict, source_path: Path) -> bytes:
    if source_path.exists():
        return source_path.read_bytes()
    raw = sec_fetcher._get(metadata["xbrl_url"])
    source_path.write_bytes(raw)
    return raw


def fetch_cloud_disclosures(
    symbols: list[str] | None = None,
    *,
    years: int = 2,
    limit_per_symbol: int | None = None,
    output_dir: Path = SCRATCH_DIR,
    sec_dir: Path = SEC_DIR,
) -> pd.DataFrame:
    """从 SEC 官方 filing 与 XBRL 实例生成云披露 DataFrame；data/ 仅用于读取原文。"""
    selected_symbols = [symbol.upper() for symbol in (symbols or CIKS)]
    unsupported = set(selected_symbols) - set(CIKS)
    if unsupported:
        raise ValueError(f"不支持的云厂商: {sorted(unsupported)}")
    if years <= 0 or (limit_per_symbol is not None and limit_per_symbol <= 0):
        raise ValueError("years 与 limit_per_symbol 必须为正数")
    source_dir = output_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for symbol in selected_symbols:
        filings = _filing_index(CIKS[symbol], years)
        if limit_per_symbol is not None:
            filings = filings[:limit_per_symbol]
        for metadata in filings:
            logger.info("解析 %s %s %s", symbol, metadata["form"], metadata["filing_date"])
            source_path = source_dir / (
                f"{symbol}_{metadata['form']}_{metadata['filing_date']}_htm.xml"
            )
            xbrl_content = _read_xbrl(metadata, source_path)
            filing_text = _read_filing_text(
                symbol, metadata, sec_dir=sec_dir, source_dir=source_dir
            )
            frames.append(
                parse_filing_disclosures(
                    symbol,
                    xbrl_content,
                    filing_text,
                    form=metadata["form"],
                    filing_date=metadata["filing_date"],
                    accession=metadata["accession"],
                    filing_url=metadata["filing_url"],
                    xbrl_url=metadata["xbrl_url"],
                )
            )
    if not frames:
        return _frame([], OUTPUT_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(
        ["symbol", "period_end", "period_type", "period_start", "filing_date"],
        ignore_index=True,
    )


def capex_guidance_event(
    *,
    symbol: str,
    announced_on: str,
    guidance_period: str,
    source_url: str,
    source_excerpt: str,
    minimum_usd: int | float | None = None,
    maximum_usd: int | float | None = None,
    point_estimate_usd: int | float | None = None,
) -> dict:
    """生成经人工核对的资本开支指引事件，数值由调用方按正式披露提供。"""
    symbol = symbol.upper()
    if symbol not in CIKS:
        raise ValueError(f"不支持的云厂商: {symbol}")
    date.fromisoformat(announced_on)
    if not guidance_period.strip() or not source_excerpt.strip():
        raise ValueError("guidance_period 与 source_excerpt 不能为空")
    if not source_url.startswith("https://"):
        raise ValueError("source_url 必须是正式披露的 HTTPS URL")
    values = (minimum_usd, maximum_usd, point_estimate_usd)
    if any(value is not None and value < 0 for value in values):
        raise ValueError("资本开支指引金额不能为负数")
    if minimum_usd is not None and maximum_usd is not None and minimum_usd > maximum_usd:
        raise ValueError("minimum_usd 不能大于 maximum_usd")
    return {
        "symbol": symbol,
        "announced_on": announced_on,
        "guidance_period": guidance_period,
        "minimum_usd": minimum_usd,
        "maximum_usd": maximum_usd,
        "point_estimate_usd": point_estimate_usd,
        "currency": "USD",
        "source_url": source_url,
        "source_excerpt": source_excerpt.strip(),
        "extraction_method": "manual_verified_issuer_disclosure",
    }


def capex_guidance_frame(events: Iterable[dict]) -> pd.DataFrame:
    """将人工核对的事件字典转换为固定字段的 DataFrame。"""
    input_fields = {
        "symbol",
        "announced_on",
        "guidance_period",
        "source_url",
        "source_excerpt",
        "minimum_usd",
        "maximum_usd",
        "point_estimate_usd",
    }
    normalized = [
        capex_guidance_event(**{key: value for key, value in event.items() if key in input_fields})
        for event in events
    ]
    return _frame(normalized, CAPEX_COLUMNS)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEC 四大云厂商 AI/云披露结构化")
    parser.add_argument("--symbols", help="逗号分隔：MSFT,GOOG,AMZN,META；默认全部")
    parser.add_argument("--years", type=int, default=2, help="回溯年数，默认 2")
    parser.add_argument("--limit", type=int, help="每家公司最多处理多少份申报，按申报日倒序")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--capex-events",
        type=Path,
        default=OUTPUT_DIR / "capex_guidance_events.json",
        help="人工核对的资本开支事件 JSON 数组",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    symbols = args.symbols.split(",") if args.symbols else None
    disclosures = fetch_cloud_disclosures(
        symbols,
        years=args.years,
        limit_per_symbol=args.limit,
        output_dir=SCRATCH_DIR,
    )
    if disclosures.empty:
        raise RuntimeError("SEC 云披露结果为空")
    disclosure_path = args.output_dir / "cloud_disclosures.csv"
    upsert_rows(
        disclosure_path,
        disclosures,
        subset=["symbol", "cloud_unit", "period_type", "period_start", "period_end"],
        sort_by=["symbol", "period_end", "period_type", "period_start", "filing_date"],
    )
    logger.info("云披露 %d 行，写入 %s", len(disclosures), disclosure_path)
    if args.capex_events:
        events = json.loads(args.capex_events.read_text(encoding="utf-8"))
        guidance = capex_guidance_frame(events)
        guidance_path = args.output_dir / "capex_guidance.csv"
        upsert_rows(
            guidance_path,
            guidance,
            subset=["symbol", "announced_on", "guidance_period"],
            sort_by=["announced_on", "symbol", "guidance_period"],
        )
        logger.info("资本开支指引 %d 行，写入 %s", len(guidance), guidance_path)

    display_columns = [
        "symbol",
        "cloud_unit",
        "availability",
        "form",
        "fiscal_period",
        "period_type",
        "period_end",
        "revenue_usd",
        "revenue_growth_pct",
    ]
    print(disclosures[display_columns].to_string(index=False))


if __name__ == "__main__":
    main()
