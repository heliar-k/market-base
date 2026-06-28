"""
Fetch CBOE volatility data: VIX, OVX, VVIX, VXEEM.
Data sources:
  - CBOE CDN CSV files (updated daily)
  - FRED fallback for VIX (VIXCLS)
"""

import logging
import re
from datetime import datetime
from io import StringIO

import pandas as pd
import requests

from ..quality import DataPoint

logger = logging.getLogger(__name__)

# CBOE CDN CSV URLs (new as of 2025+)
CBOE_URLS = {
    "VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "OVX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/OVX_History.csv",
    "VVIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv",
    "VIX9D": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    "VXEEM": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXEEM_History.csv",
}


def fetch_cboe_volatility() -> list[DataPoint]:
    """
    Fetch VIX, OVX, and related vol data from CBOE CDN CSVs.
    Falls back to FRED for VIX if CBOE fails.
    """
    results = []

    # --- VIX from CBOE CSV (more timely) ---
    vix_dp = _fetch_cboe_csv("VIX", CBOE_URLS["VIX"])
    results.append(vix_dp)

    # --- OVX from CBOE CSV ---
    ovx_dp = _fetch_cboe_csv("OVX", CBOE_URLS["OVX"])
    results.append(ovx_dp)

    return results


def _fetch_cboe_csv(name: str, url: str) -> DataPoint:
    """Fetch a single CBOE CSV and return the latest value."""
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

        # Date column
        date_col = _find_column(df.columns, ["DATE", "Date", "date"])
        if not date_col:
            raise ValueError(f"No date column found in {list(df.columns)}")

        # Value column — for VIX it's 'CLOSE', for OVX it's 'OVX'
        if name == "OVX":
            val_col = _find_column(df.columns, ["OVX", "CLOSE", "Close", "close"])
        else:
            val_col = _find_column(df.columns, ["CLOSE", "Close", "close", name])

        if not val_col:
            raise ValueError(f"No value column found in {list(df.columns)}")

        dp.value = round(float(latest[val_col]), 2)
        dp.as_of = _normalize_date(str(latest[date_col]))
        dp.mark_ok()
        logger.info(f"  CBOE {name}: {dp.value} (as_of={dp.as_of})")

    except Exception as e:
        logger.warning(f"CBOE {name} CSV failed: {e}")
        # Fallback to FRED for VIX only
        if name == "VIX":
            logger.info("  Falling back to FRED VIXCLS...")
            try:
                from .fred_fetcher import fetch_single_fred

                fred_dp = fetch_single_fred("VIX", "VIXCLS")
                if fred_dp.value is not None:
                    dp.value = fred_dp.value
                    dp.as_of = fred_dp.as_of
                    dp.source = "FRED / VIXCLS (CBOE fallback)"
                    dp.mark_ok()
                else:
                    dp.mark_error("Both CBOE CSV and FRED VIXCLS returned no value")
            except Exception as e2:
                dp.mark_error(f"VIX fallback also failed: {e2}")
        else:
            dp.mark_error(str(e))

    return dp


def _find_column(columns: list, candidates: list) -> str:
    """Find the first matching column name from candidates."""
    for c in candidates:
        for col in columns:
            if col.strip().upper() == c.upper():
                return col
    return ""


def _normalize_date(raw: str) -> str:
    """Convert various date formats to ISO YYYY-MM-DD."""
    raw = str(raw).strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # MM/DD/YYYY (CBOE format)
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        return datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")
    # MM-DD-YYYY
    if re.match(r"^\d{2}-\d{2}-\d{4}$", raw):
        return datetime.strptime(raw, "%m-%d-%Y").strftime("%Y-%m-%d")
    # Try common formats
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(f"Could not normalize date: {raw}")
    return raw


def compute_vix_term_structure() -> list[DataPoint]:
    """
    Compute VIX term structure indicators.
    Uses VIX9D (9-day) and VIX (30-day) from CBOE.
    """
    results = []

    # Fetch VIX (already fetched above but re-fetch for independence)
    vix_dp = _fetch_cboe_csv("VIX", CBOE_URLS["VIX"])
    vix9d_dp = _fetch_cboe_csv("VIX9D", CBOE_URLS["VIX9D"])

    # Term structure slope: VIX - VIX9D
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
        ts_dp.mark_warn("VIX or VIX9D data missing for term structure")
    results.append(ts_dp)

    logger.info(
        f"VIX term structure: slope={ts_dp.value} "
        f"(VIX={vix_dp.value}, VIX9D={vix9d_dp.value})"
    )
    return results
