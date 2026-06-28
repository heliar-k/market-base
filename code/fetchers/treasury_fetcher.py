"""
Fetch U.S. Treasury Daily Yield Curve rates.
Source: https://home.treasury.gov/sites/default/files/interest-rates/yield.xml
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from ..quality import DataPoint

logger = logging.getLogger(__name__)

# XML tag -> metric name mapping
XML_TAG_MAP = {
    "BC_1MONTH": "DGS1MO",
    "BC_3MONTH": "DGS3MO",
    "BC_6MONTH": "DGS6MO",
    "BC_1YEAR": "DGS1",
    "BC_2YEAR": "DGS2",
    "BC_3YEAR": "DGS3",
    "BC_5YEAR": "DGS5",
    "BC_7YEAR": "DGS7",
    "BC_10YEAR": "DGS10",
    "BC_20YEAR": "DGS20",
    "BC_30YEAR": "DGS30",
}

# Fallback: also try the paginated XML feed for more history
XML_FEED_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml?data=daily_treasury_yield_curve"
    "&field_tdr_date_value=2026"
)
DAILY_XML_URL = "https://home.treasury.gov/sites/default/files/interest-rates/yield.xml"


def fetch_treasury_yields() -> list[DataPoint]:
    """Fetch latest Treasury yield curve from daily XML. Returns list of DataPoints."""
    results = []

    # Try daily XML first (simpler, always has latest)
    try:
        resp = requests.get(DAILY_XML_URL, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        return _parse_daily_xml(root)
    except Exception as e:
        logger.warning(f"Daily XML failed ({e}), trying paginated feed...")

    # Fallback: paginated XML feed for 2026
    try:
        resp = requests.get(XML_FEED_URL, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        return _parse_atom_feed(root)
    except Exception as e:
        logger.error(f"All Treasury sources failed: {e}")

    # All failed — return error points
    for xml_tag, metric_name in XML_TAG_MAP.items():
        dp = DataPoint(
            metric=metric_name,
            source="U.S. Treasury Daily Yield Curve",
            formula="yield_curve.yield_pct",
        )
        dp.mark_error("Failed to fetch Treasury data from all sources")
        results.append(dp)
    return results


def _parse_daily_xml(root: ET.Element) -> list[DataPoint]:
    """Parse the daily yield.xml structure."""
    results = []

    # Find all G_NEW_DATE entries
    date_entries = root.findall(".//G_NEW_DATE")
    if not date_entries:
        raise ValueError("No G_NEW_DATE entries found in daily XML")

    # Get the last (most recent) entry
    latest = date_entries[-1]

    # Extract date from NEW_DATE (format: MM-DD-YYYY)
    new_date_el = latest.find("NEW_DATE")
    if new_date_el is None or not new_date_el.text:
        raise ValueError("NEW_DATE not found in latest entry")
    as_of = _parse_date_mdy(new_date_el.text.strip())

    # Extract BC_* values from G_BC_CAT
    bc_cat = latest.find(".//G_BC_CAT")
    if bc_cat is None:
        raise ValueError("G_BC_CAT not found in latest entry")

    for xml_tag, metric_name in XML_TAG_MAP.items():
        dp = DataPoint(
            metric=metric_name,
            source="U.S. Treasury Daily Yield Curve / yield.xml",
            formula="yield_curve.yield_pct; most recent entry",
        )
        el = bc_cat.find(xml_tag)
        if el is not None and el.text:
            try:
                dp.value = round(float(el.text), 2)
                dp.as_of = as_of
                dp.mark_ok()
            except ValueError:
                dp.mark_error(f"Invalid value '{el.text}' for {xml_tag}")
        else:
            # Try alternate tag names
            alt_tag = xml_tag.replace("YEAR", "YEARDISPLAY")
            alt_el = bc_cat.find(alt_tag) if alt_tag != xml_tag else None
            if alt_el is not None and alt_el.text:
                try:
                    dp.value = round(float(alt_el.text), 2)
                    dp.as_of = as_of
                    dp.mark_ok()
                except ValueError:
                    dp.mark_error(f"Tag {xml_tag} not found in XML")
            else:
                dp.mark_warn(f"Tag {xml_tag} not found in XML")
        results.append(dp)

    logger.info(f"Treasury yields fetched: as_of={as_of}, {len(results)} tenors")
    return results


def _parse_atom_feed(root: ET.Element) -> list[DataPoint]:
    """Parse the paginated Atom XML feed."""
    results = []
    ns = "{http://www.w3.org/2005/Atom}"
    d_ns = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
    m_ns = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"

    entries = root.findall(f"{ns}entry")
    if not entries:
        raise ValueError("No entries in Atom feed")

    # Get the last entry (most recent)
    latest_entry = entries[-1]
    content = latest_entry.find(f"{ns}content")
    if content is None:
        raise ValueError("No content in entry")

    props = content.find(f"{m_ns}properties")
    if props is None:
        raise ValueError("No properties in content")

    # Extract date from NEW_DATE or as fallback from Id
    new_date_el = props.find(f"{d_ns}NEW_DATE")
    if new_date_el is not None and new_date_el.text:
        as_of = _parse_date_mdy(new_date_el.text.strip())
    else:
        as_of = "unknown"

    # Map Atom XML element names to metric names
    # (same mapping as XML_TAG_MAP at module level)
    for xml_tag, metric_name in XML_TAG_MAP.items():
        dp = DataPoint(
            metric=metric_name,
            source="U.S. Treasury Daily Yield Curve / Atom feed",
            formula="yield_curve.yield_pct; most recent entry",
        )
        el = props.find(f"{d_ns}{xml_tag}")
        if el is not None and el.text:
            try:
                dp.value = round(float(el.text), 2)
                dp.as_of = as_of
                dp.mark_ok()
            except ValueError:
                dp.mark_error(f"Invalid value for {xml_tag}")
        else:
            dp.mark_warn(f"Tag {xml_tag} not found in feed")
        results.append(dp)

    logger.info(f"Treasury yields (Atom): as_of={as_of}, {len(results)} tenors")
    return results


def _parse_date_mdy(raw: str) -> str:
    """Convert MM-DD-YYYY or DD-MON-YY to ISO YYYY-MM-DD."""
    raw = raw.strip()
    # MM-DD-YYYY
    if re.match(r"^\d{2}-\d{2}-\d{4}$", raw):
        return datetime.strptime(raw, "%m-%d-%Y").strftime("%Y-%m-%d")
    # DD-MON-YY (e.g., 03-JUN-26)
    if re.match(r"^\d{2}-[A-Z]{3}-\d{2}$", raw, re.IGNORECASE):
        return datetime.strptime(raw, "%d-%b-%y").strftime("%Y-%m-%d")
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    logger.warning(f"Unknown date format: {raw}")
    return raw


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    results = fetch_treasury_yields()
    ok = sum(1 for r in results if r.qa_status.value in ("ok", "warn"))
    print(f"Treasury: {ok}/{len(results)} fetched")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "treasury" / "treasury_yields.csv", results)
    print("  → data/treasury/treasury_yields.csv")
