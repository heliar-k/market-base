"""
Fetch core asset prices via yfinance.
Needs SOCKS5 proxy: socks5://127.0.0.1:7890
"""

import logging
import os

from ..config import config
from ..quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)

# CRITICAL: Set proxy env vars BEFORE importing yfinance (it reads them at import time)
if config.https_proxy:
    os.environ["HTTPS_PROXY"] = config.https_proxy
if config.http_proxy:
    os.environ["HTTP_PROXY"] = config.http_proxy

import yfinance as yf  # noqa: E402


def _fetch_ticker(ticker: str, name: str) -> DataPoint:
    """Fetch a single yfinance ticker and return a DataPoint."""
    dp = DataPoint(
        metric=name,
        source="Yahoo Finance / yfinance",
        formula="asset_prices.close, daily close",
    )
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            dp.mark_error("No data returned from yfinance")
            return dp
        close = hist["Close"].iloc[-1]
        ts = hist.index[-1].to_pydatetime()
        dp.value = round(float(close), 4)
        dp.as_of = ts.strftime("%Y-%m-%d")
        dp.mark_ok()
    except Exception as e:
        dp.mark_error(str(e))
    return dp


def fetch_all_assets() -> list[DataPoint]:
    """Fetch all configured yfinance tickers. Returns list of DataPoints."""
    results = []
    for name, ticker in config.yf_tickers.items():
        logger.info(f"Fetching {name} ({ticker})...")
        dp = _fetch_ticker(ticker, name)
        results.append(dp)
        status = "✓" if dp.qa_status == QAStatus.OK else "✗"
        logger.info(f"  {status} {name}: {dp.value} (as_of={dp.as_of})")
    return results


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    results = fetch_all_assets()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"yfinance: {ok}/{len(results)} OK")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "yfinance" / "asset_prices.csv", results)
    print("  → data/yfinance/asset_prices.csv")
