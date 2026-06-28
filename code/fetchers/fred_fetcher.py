"""
Fetch economic indicators from FRED API.
Requires FRED_API_KEY in .env file.
"""

import logging

from fredapi import Fred

from ..config import config
from ..quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)


def _get_fred() -> Fred:
    if not config.fred_api_key:
        raise ValueError("FRED_API_KEY not configured")
    return Fred(api_key=config.fred_api_key)


def _fetch_one_fred(fred: Fred, name: str, series_id: str) -> DataPoint:
    """Core fetch logic shared by fetch_all_fred and fetch_single_fred."""
    dp = DataPoint(
        metric=name,
        source=f"FRED / {series_id}",
        formula="time_series.value; most recent non-null observation",
    )
    try:
        series = fred.get_series(series_id)
        valid = series.dropna()
        if valid.empty:
            dp.mark_error("No valid data in series")
            return dp
        dp.value = round(float(valid.iloc[-1]), 6)
        dp.as_of = valid.index[-1].strftime("%Y-%m-%d")
        dp.mark_ok()
    except Exception as e:
        dp.mark_error(str(e))
    return dp


def fetch_all_fred() -> list[DataPoint]:
    """Fetch all configured FRED series. Returns list of DataPoints."""
    fred = _get_fred()
    results = []
    for name, series_id in config.fred_series.items():
        logger.info(f"Fetching {name} ({series_id})...")
        dp = _fetch_one_fred(fred, name, series_id)
        status = "✓" if dp.qa_status == QAStatus.OK else "✗"
        logger.info(f"  {status} {name}: {dp.value} (as_of={dp.as_of})")
        results.append(dp)
    return results


def fetch_single_fred(name: str, series_id: str) -> DataPoint:
    """Fetch a single FRED series by name + ID."""
    fred = _get_fred()
    return _fetch_one_fred(fred, name, series_id)


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    config.validate()
    results = fetch_all_fred()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"FRED: {ok}/{len(results)} OK")

    # 保存到 data/fred/fred_series.csv
    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "fred" / "fred_series.csv", results)
    print("  → data/fred/fred_series.csv")
