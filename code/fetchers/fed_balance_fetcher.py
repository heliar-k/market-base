"""
Compute derived Fed liquidity metrics from FRED data.

Reads from data/fred/fred_series.csv (already fetched by fred_fetcher),
computes derived indicators: SOFR-IORB spread and Net Liquidity.

Units:
  - FRED RRPONTSYD: Billions of USD → converted to millions here
  - FRED WTREGEN, WRESBAL, WALCL: Millions of USD
  - All output in millions for consistency
"""

import logging

import pandas as pd

from ..quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)


def _read_latest_fred() -> dict:
    """Read the latest row from data/fred/fred_series.csv."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    csv_path = root / "data" / "fred" / "fred_series.csv"

    if not csv_path.exists():
        logger.error("fred_series.csv not found — run ./bin/fetch_fred first")
        return {}

    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
    if df.empty:
        logger.error("fred_series.csv is empty")
        return {}

    latest = df.iloc[-1]
    return {
        col: float(latest[col]) if pd.notna(latest[col]) else None for col in df.columns
    }


def compute_net_liquidity() -> list[DataPoint]:
    """
    Compute net liquidity and related metrics from FRED CSV.
    Net Liquidity = WALCL - RRP - TGA (millions of USD)
    """
    results = []
    row = _read_latest_fred()

    if not row:
        # All failed
        for metric in [
            "RRP",
            "TGA",
            "RESERVES",
            "SOFR",
            "IORB",
            "SOFR_IORB_SPREAD",
            "NET_LIQUIDITY",
        ]:
            dp = DataPoint(
                metric=metric,
                source="Fed H.4.1 via FRED",
                formula="fred_series.csv",
            )
            dp.mark_error("FRED data not available")
            results.append(dp)
        return results

    # --- RRP: normalize billions → millions ---
    rrp_b = row.get("RRPONTSYD")
    if rrp_b is not None:
        rrp_m = rrp_b * 1000.0
        rrp_dp = DataPoint(
            metric="RRP",
            source="FRED / RRPONTSYD",
            formula="RRPONTSYD (billions) * 1000 = millions",
            value=round(rrp_m, 2),
        )
        rrp_dp.mark_ok()
    else:
        rrp_m = None
        rrp_dp = DataPoint(
            metric="RRP",
            source="FRED / RRPONTSYD",
            formula="RRPONTSYD (billions) * 1000 = millions",
        )
        rrp_dp.mark_error("RRPONTSYD missing")
    results.append(rrp_dp)

    # --- TGA ---
    tga = row.get("WTREGEN")
    tga_dp = DataPoint(
        metric="TGA",
        source="FRED / WTREGEN",
        formula="WTREGEN (millions of USD)",
        value=round(tga, 2) if tga else None,
    )
    tga_dp.mark_ok() if tga else tga_dp.mark_error("WTREGEN missing")
    results.append(tga_dp)

    # --- RESERVES ---
    res = row.get("WRESBAL")
    res_dp = DataPoint(
        metric="RESERVES",
        source="FRED / WRESBAL",
        formula="WRESBAL (millions of USD)",
        value=round(res, 2) if res else None,
    )
    res_dp.mark_ok() if res else res_dp.mark_error("WRESBAL missing")
    results.append(res_dp)

    # --- SOFR ---
    sofr = row.get("SOFR")
    sofr_dp = DataPoint(
        metric="SOFR",
        source="FRED / SOFR",
        formula="SOFR (%)",
        value=round(sofr, 4) if sofr else None,
    )
    sofr_dp.mark_ok() if sofr else sofr_dp.mark_error("SOFR missing")
    results.append(sofr_dp)

    # --- IORB ---
    iorb = row.get("IORB")
    iorb_dp = DataPoint(
        metric="IORB",
        source="FRED / IORB",
        formula="IORB (%)",
        value=round(iorb, 4) if iorb else None,
    )
    iorb_dp.mark_ok() if iorb else iorb_dp.mark_error("IORB missing")
    results.append(iorb_dp)

    # --- SOFR-IORB spread (bp) ---
    spread_dp = DataPoint(
        metric="SOFR_IORB_SPREAD",
        source="FRED / SOFR - IORB",
        formula="(SOFR - IORB) * 100; bp",
    )
    if sofr is not None and iorb is not None:
        spread_dp.value = round((sofr - iorb) * 100, 2)
        spread_dp.mark_ok()
    else:
        spread_dp.mark_error("SOFR or IORB missing")
    results.append(spread_dp)

    # --- Net Liquidity (millions of USD) ---
    walcl = row.get("WALCL")
    nl_dp = DataPoint(
        metric="NET_LIQUIDITY",
        source="Fed H.4.1 via FRED",
        formula="WALCL - RRP - TGA (millions of USD)",
    )
    if rrp_m is not None and tga is not None and walcl is not None:
        nl = walcl - rrp_m - tga
        nl_dp.value = round(nl, 2)
        nl_dp.mark_ok()
    else:
        nl_dp.mark_error("WALCL, RRP or TGA missing")
    results.append(nl_dp)

    return results


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    results = compute_net_liquidity()
    names = [r.metric for r in results]
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"Fed Balance: {ok}/{len(results)} OK → {names}")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "fed_balance" / "liquidity.csv", results)
    print("  → data/fed_balance/liquidity.csv")
