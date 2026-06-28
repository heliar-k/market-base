"""
Fetch Fed H.4.1 data: Reverse Repo (RRP), Treasury General Account (TGA),
Reserve Balances, and compute Net Liquidity.

Data comes from FRED series (already in fred_fetcher).
This module computes derived metrics: net_liquidity, SOFR-IORB spread.
"""

import logging

from ..quality import DataPoint
from .fred_fetcher import fetch_single_fred

logger = logging.getLogger(__name__)


def compute_net_liquidity() -> list[DataPoint]:
    """
    Compute net liquidity and related metrics.
    Net Liquidity ≈ Total Fed Assets - RRP - TGA

    IMPORTANT: FRED series units differ:
      - WALCL, WTREGEN, WRESBAL: Millions of USD
      - RRPONTSYD: Billions of USD (must convert to millions)
      - SOFR, IORB: Percent
    """
    results = []

    # Fetch data from FRED
    rrp_dp = fetch_single_fred("RRP", "RRPONTSYD")
    tga_dp = fetch_single_fred("TGA", "WTREGEN")
    reserves_dp = fetch_single_fred("RESERVES", "WRESBAL")
    sofr_dp = fetch_single_fred("SOFR", "SOFR")
    iorb_dp = fetch_single_fred("IORB", "IORB")

    # --- Unit normalization: RRPONTSYD is in Billions, convert to Millions ---
    if rrp_dp.value is not None:
        rrp_millions = rrp_dp.value * 1000.0
    else:
        rrp_millions = None

    # Store the normalized RRP in millions
    rrp_dp.metric = "RRP"
    rrp_dp.formula = "RRPONTSYD (billions) * 1000 = millions"
    if rrp_dp.value is not None:
        rrp_dp.value = round(rrp_millions, 2)

    tga_dp.metric = "TGA"
    reserves_dp.metric = "RESERVES"
    sofr_dp.metric = "SOFR"
    iorb_dp.metric = "IORB"

    results.extend([rrp_dp, tga_dp, reserves_dp, sofr_dp, iorb_dp])

    # Compute SOFR-IORB spread (bp)
    spread_dp = DataPoint(
        metric="SOFR_IORB_SPREAD",
        source="FRED / SOFR - IORB",
        formula="SOFR - IORB; bp = (SOFR - IORB) * 100",
    )
    if sofr_dp.value is not None and iorb_dp.value is not None:
        spread_bp = (sofr_dp.value - iorb_dp.value) * 100
        spread_dp.value = round(spread_bp, 2)
        spread_dp.as_of = sofr_dp.as_of or iorb_dp.as_of
        spread_dp.mark_ok()
    else:
        spread_dp.mark_warn("SOFR or IORB data missing")
    results.append(spread_dp)

    # Compute Net Liquidity (all in millions of USD)
    nl_dp = DataPoint(
        metric="NET_LIQUIDITY",
        source="Fed H.4.1 via FRED",
        formula="WALCL - RRP - TGA (millions of USD)",
    )
    if rrp_millions is not None and tga_dp.value is not None:
        try:
            ta_dp = fetch_single_fred("WALCL", "WALCL")
            if ta_dp.value is not None:
                nl = ta_dp.value - rrp_millions - tga_dp.value
                nl_dp.value = round(nl, 2)
                nl_dp.as_of = tga_dp.as_of or rrp_dp.as_of
                nl_dp.mark_ok()
            else:
                raise ValueError("WALCL unavailable")
        except Exception:
            # Fallback: approximate Fed total assets (~6.7T range as of 2026)
            nl_dp.value = round(6700000.0 - rrp_millions - tga_dp.value, 2)
            nl_dp.as_of = tga_dp.as_of or rrp_dp.as_of
            nl_dp.mark_warn("Using approximate Fed total assets (~6.7T)")
    else:
        nl_dp.mark_error("RRP or TGA data missing")
    results.append(nl_dp)

    return results
