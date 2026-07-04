"""
Fetch CBOE volatility data not covered by FRED: OVX, VIX9D.
VIX is already in FRED fred_series.csv — not duplicated here.

Data sources:
  - CBOE CDN CSV files (updated daily)
"""

import logging
import re
from datetime import datetime
from io import StringIO

import pandas as pd
import requests

from .quality import DataPoint

logger = logging.getLogger(__name__)

CBOE_URLS = {
    "OVX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/OVX_History.csv",
    "VIX9D": (
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv"
    ),
    # VIX is still fetched on-demand in compute_vix_term_structure(),
    # but NOT saved to CSV (FRED fred_series.csv already has it).
    "VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
}


def fetch_cboe_volatility() -> list[DataPoint]:
    """Fetch OVX from CBOE CDN. VIX is handled by FRED fetcher."""
    results = []

    ovx_dp = _fetch_cboe_csv("OVX", CBOE_URLS["OVX"])
    results.append(ovx_dp)

    return results


def _fetch_cboe_csv(name: str, url: str) -> DataPoint:
    """Fetch a single CBOE CSV and return the latest close value."""
    dp = DataPoint(
        metric=name,
        source=f"Cboe CDN / {name}_History.csv",
        formula="time_series.value; most recent close",
    )
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            raise ValueError("Empty CSV")

        latest = df.iloc[-1]

        date_col = _find_column(df.columns, ["DATE", "Date", "date"])
        if not date_col:
            raise ValueError(f"No date column in {list(df.columns)}")

        if name == "OVX":
            val_col = _find_column(df.columns, ["OVX", "CLOSE", "Close", "close"])
        else:
            val_col = _find_column(df.columns, ["CLOSE", "Close", "close", name])

        if not val_col:
            raise ValueError(f"No value column in {list(df.columns)}")

        dp.value = round(float(latest[val_col]), 2)
        dp.as_of = _normalize_date(str(latest[date_col]))
        dp.mark_ok()
        logger.info(f"  CBOE {name}: {dp.value} (as_of={dp.as_of})")

    except Exception as e:
        dp.mark_error(str(e))
        logger.warning(f"CBOE {name} failed: {e}")

    return dp


def _find_column(columns: list, candidates: list) -> str:
    """Find the first matching column name from candidates (case-insensitive)."""
    for c in candidates:
        for col in columns:
            if col.strip().upper() == c.upper():
                return col
    return ""


def _normalize_date(raw: str) -> str:
    """Convert various date formats to ISO YYYY-MM-DD."""
    raw = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        return datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")
    if re.match(r"^\d{2}-\d{2}-\d{4}$", raw):
        return datetime.strptime(raw, "%m-%d-%Y").strftime("%Y-%m-%d")
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(f"Could not normalize date: {raw}")
    return raw


def compute_vix_term_structure() -> list[DataPoint]:
    """
    Compute VIX term structure: VIX (30-day) — VIX9D (9-day).
    Fetches both from CBOE CDN on-demand (VIX is not saved to CSV).
    """
    results = []

    vix_dp = _fetch_cboe_csv("VIX", CBOE_URLS["VIX"])
    vix9d_dp = _fetch_cboe_csv("VIX9D", CBOE_URLS["VIX9D"])

    ts_dp = DataPoint(
        metric="VIX_TERM_SLOPE",
        source="CBOE / VIX_History.csv + VIX9D_History.csv",
        formula=(
            "VIX (30-day) - VIX9D (9-day); positive=contango, negative=backwardation"
        ),
    )
    if vix_dp.value is not None and vix9d_dp.value is not None:
        ts_dp.value = round(float(vix_dp.value - vix9d_dp.value), 2)
        ts_dp.as_of = vix_dp.as_of or vix9d_dp.as_of
        ts_dp.mark_ok()
    else:
        ts_dp.mark_warn("VIX or VIX9D data missing")
    results.append(ts_dp)

    logger.info(
        f"VIX term structure: slope={ts_dp.value} "
        f"(VIX={vix_dp.value}, VIX9D={vix9d_dp.value})"
    )
    return results


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    vol_results = fetch_cboe_volatility()
    ts_results = compute_vix_term_structure()
    all_results = vol_results + ts_results

    ok = sum(1 for r in all_results if r.qa_status.value in ("ok", "warn"))
    print(f"CBOE: {ok}/{len(all_results)} fetched")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "cboe" / "volatility.csv", all_results)
    print("  → data/cboe/volatility.csv")
