from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import ROOT
from . import sec_fetcher
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

SCRATCH_DIR = ROOT / ".scratch" / "sec_cashflow"
OUTPUT_DIR = ROOT / "data" / "financials"
CIKS = {
    "MSFT": "789019",
    "GOOG": "1652044",
    "AMZN": "1018724",
    "META": "1326801",
}
CFO_TAG = "NetCashProvidedByUsedInOperatingActivities"
CAPEX_TAGS = {
    "MSFT": "PaymentsToAcquirePropertyPlantAndEquipment",
    "GOOG": "PaymentsToAcquirePropertyPlantAndEquipment",
    "AMZN": "PaymentsToAcquireProductiveAssets",
    "META": "PaymentsToAcquirePropertyPlantAndEquipment",
}
YTD_DAYS = {
    "Q1": (80, 100),
    "Q2": (170, 195),
    "Q3": (260, 285),
    "FY": (350, 380),
}
FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
OUTPUT_COLUMNS = [
    "symbol",
    "date",
    "period_start",
    "Capital Expenditure",
    "Operating Cash Flow",
    "Free Cash Flow",
    "cfo_tag",
    "capex_tag",
    "fiscal_period",
    "derivation",
    "accession",
    "form",
    "filing_date",
    "frame",
    "source",
]
FACT_COLUMNS = [
    "date",
    "period_start",
    "value",
    "fiscal_period",
    "derivation",
    "accession",
    "form",
    "filing_date",
    "frame",
]


def companyfacts_url(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol not in CIKS:
        raise ValueError(f"不支持的公司: {symbol}")
    return f"{sec_fetcher.DATA_BASE}/api/xbrl/companyfacts/CIK{int(CIKS[symbol]):010d}.json"


def _ytd_facts(payload: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    """只保留与 SEC fiscal period 相符的 YTD 期间，排除单季、TTM 和即时值。"""
    facts = payload["facts"]["us-gaap"][tag]["units"]["USD"]
    rows = []
    for fact in facts:
        if fact.get("form") not in FORMS or "start" not in fact:
            continue
        fp = fact.get("fp")
        if fp not in YTD_DAYS:
            continue
        start = date.fromisoformat(fact["start"])
        end = date.fromisoformat(fact["end"])
        minimum, maximum = YTD_DAYS[fp]
        if not minimum <= (end - start).days <= maximum:
            continue
        rows.append(
            {
                "start": fact["start"],
                "end": fact["end"],
                "fp": fp,
                "value": fact["val"],
                "accession": fact["accn"],
                "form": fact["form"],
                "filing_date": fact["filed"],
                "frame": fact.get("frame", ""),
            }
        )

    return rows


def _source_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        row["start"],
        row["fp"],
        row["end"],
        row["accession"],
        row["form"],
        row["filing_date"],
    )


def _common_period_groups(
    cfo_facts: list[dict[str, Any]], capex_facts: list[dict[str, Any]]
) -> tuple[list[dict[str, dict[str, Any]]], list[dict[str, dict[str, Any]]]]:
    """每个期间选择 CFO 与 Capex 共同出现的最新同一份申报。"""
    capex_by_source = {_source_key(row): row for row in capex_facts}
    latest = {}
    for cfo in cfo_facts:
        capex = capex_by_source.get(_source_key(cfo))
        if capex is None:
            continue
        key = (cfo["start"], cfo["fp"], cfo["end"])
        previous = latest.get(key)
        if previous is None or (cfo["filing_date"], cfo["accession"]) > (
            previous[0]["filing_date"],
            previous[0]["accession"],
        ):
            latest[key] = (cfo, capex)

    grouped = {}
    for (start, fp, end), pair in latest.items():
        periods = grouped.setdefault(start, {})
        previous = periods.get(fp)
        if previous is not None and previous[0]["end"] != end:
            raise ValueError(f"同一 fiscal year 的 {fp} 出现不同期末: {previous[0]['end']} / {end}")
        periods[fp] = pair
    cfo_groups = [{fp: pair[0] for fp, pair in periods.items()} for periods in grouped.values()]
    capex_groups = [{fp: pair[1] for fp, pair in periods.items()} for periods in grouped.values()]
    return cfo_groups, capex_groups


def _single_quarters(period_groups: list[dict[str, dict[str, Any]]]) -> pd.DataFrame:
    rows = []
    for periods in period_groups:
        previous = None
        for ytd_fp, quarter_fp in (("Q1", "Q1"), ("Q2", "Q2"), ("Q3", "Q3"), ("FY", "Q4")):
            current = periods.get(ytd_fp)
            if current is None:
                break
            value = current["value"] if previous is None else current["value"] - previous["value"]
            period_start = (
                current["start"]
                if previous is None
                else (date.fromisoformat(previous["end"]) + timedelta(days=1)).isoformat()
            )
            derivation = "Q1 YTD" if previous is None else f"{ytd_fp} YTD - {previous['fp']} YTD"
            rows.append(
                {
                    "date": current["end"],
                    "period_start": period_start,
                    "value": value,
                    "fiscal_period": quarter_fp,
                    "derivation": derivation,
                    "accession": current["accession"],
                    "form": current["form"],
                    "filing_date": current["filing_date"],
                    "frame": current["frame"],
                }
            )
            previous = current
    return pd.DataFrame(rows, columns=FACT_COLUMNS)


def companyfacts_to_quarterly(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    """将 SEC companyfacts 的 CFO/Capex YTD 值转换为带审计字段的单季 DataFrame。"""
    symbol = symbol.upper()
    if symbol not in CIKS:
        raise ValueError(f"不支持的公司: {symbol}")
    if int(payload["cik"]) != int(CIKS[symbol]):
        raise ValueError(f"companyfacts CIK 与 {symbol} 不符: {payload['cik']}")

    capex_tag = CAPEX_TAGS[symbol]
    cfo_groups, capex_groups = _common_period_groups(
        _ytd_facts(payload, CFO_TAG), _ytd_facts(payload, capex_tag)
    )
    cfo = _single_quarters(cfo_groups)
    capex = _single_quarters(capex_groups)
    if (capex["value"] < 0).any():
        raise ValueError(f"{symbol} {capex_tag} 出现负的现金支出原始值")

    merged = cfo.merge(
        capex,
        on=["date", "period_start"],
        how="inner",
        suffixes=("_cfo", "_capex"),
        validate="one_to_one",
    )
    for field in ("fiscal_period", "derivation", "accession", "form", "filing_date"):
        mismatch = merged[f"{field}_cfo"] != merged[f"{field}_capex"]
        if mismatch.any():
            row = merged.loc[mismatch].iloc[0]
            raise ValueError(f"{symbol} {row['date']} 的 CFO/Capex {field} 来源不一致")

    result = pd.DataFrame(
        {
            "symbol": symbol,
            "date": merged["date"],
            "period_start": merged["period_start"],
            "Capital Expenditure": -merged["value_capex"],
            "Operating Cash Flow": merged["value_cfo"],
            "cfo_tag": CFO_TAG,
            "capex_tag": capex_tag,
            "fiscal_period": merged["fiscal_period_cfo"],
            "derivation": merged["derivation_cfo"],
            "accession": merged["accession_cfo"],
            "form": merged["form_cfo"],
            "filing_date": merged["filing_date_cfo"],
            "frame": merged["frame_cfo"].where(merged["frame_cfo"] != "", merged["frame_capex"]),
            "source": companyfacts_url(symbol),
        }
    )
    result["Free Cash Flow"] = result["Operating Cash Flow"] + result["Capital Expenditure"]
    return result[OUTPUT_COLUMNS].sort_values("date").reset_index(drop=True)


def fetch_companyfacts(symbol: str) -> dict[str, Any]:
    return json.loads(sec_fetcher._get(companyfacts_url(symbol)))


def fetch_cashflow(symbol: str) -> pd.DataFrame:
    return companyfacts_to_quarterly(fetch_companyfacts(symbol), symbol)


def fetch_cashflows(symbols: Iterable[str] | None = None) -> dict[str, pd.DataFrame]:
    selected = tuple(CIKS) if symbols is None else tuple(symbol.upper() for symbol in symbols)
    return {symbol: fetch_cashflow(symbol) for symbol in selected}


def main() -> None:
    parser = argparse.ArgumentParser(description="SEC companyfacts 单季现金流")
    parser.add_argument("--symbols", default=",".join(CIKS), help="默认 %(default)s")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",")]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in fetch_cashflows(symbols).items():
        if frame.empty:
            raise ValueError(f"{symbol} 没有可输出的完整单季现金流")
        destination = args.output_dir / symbol / "cashflow_quarterly.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        upsert_timeseries(
            destination,
            frame.set_index("date"),
            column_order=OUTPUT_COLUMNS,
        )
        latest = frame.iloc[-1]
        logger.info(
            "%s 最新 %s CFO=%s Capex=%s FCF=%s → %s",
            symbol,
            latest["date"],
            latest["Operating Cash Flow"],
            latest["Capital Expenditure"],
            latest["Free Cash Flow"],
            destination,
        )


if __name__ == "__main__":
    main()
