"""
Global configuration for the timsun replication pipeline.
Loads from .env file and provides typed config access.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load .env if present
_env_path = ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")


@dataclass
class Config:
    """Pipeline configuration."""

    # Data directories
    data_dir: Path = ROOT / "data"
    raw_dir: Path = ROOT / "data" / "raw"

    # API keys
    fred_api_key: str = field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))

    # Proxy for yfinance (SOCKS5)
    https_proxy: str = field(default_factory=lambda: os.getenv("HTTPS_PROXY", ""))
    http_proxy: str = field(default_factory=lambda: os.getenv("HTTP_PROXY", ""))

    # --- yfinance tickers ---
    YF_TICKERS: dict = field(
        default_factory=lambda: {
            "SPX": "^GSPC",
            "NDX": "^NDX",
            "RUT": "^RUT",
            "DJI": "^DJI",
            "DXY": "DX-Y.NYB",
            "BTC": "BTC-USD",
            "WTI": "CL=F",
            "Brent": "BZ=F",
            "Gold": "GC=F",
            "Silver": "SI=F",
            "Copper": "HG=F",
            "TLT": "TLT",
            "HYG": "HYG",
            "LQD": "LQD",
        }
    )

    # --- FRED series IDs ---
    FRED_SERIES: dict = field(
        default_factory=lambda: {
            # Volatility
            "VIX": "VIXCLS",
            # Credit spreads
            "HY_OAS": "BAMLH0A0HYM2",
            "IG_OAS": "BAMLC0A0CM",
            # Inflation expectations
            "T10YIE": "T10YIE",  # 10Y breakeven
            "T5YIE": "T5YIE",  # 5Y breakeven
            "T5YIFR": "T5YIFR",  # 5Y5Y forward
            # TIPS real yields (all tenors for BEI computation)
            "DFII5": "DFII5",
            "DFII7": "DFII7",
            "DFII10": "DFII10",
            "DFII20": "DFII20",
            "DFII30": "DFII30",
            # Financial conditions
            "NFCI": "NFCI",
            # Liquidity
            "RRPONTSYD": "RRPONTSYD",  # Reverse Repo
            "WTREGEN": "WTREGEN",  # TGA
            # Reserves
            "WRESBAL": "WRESBAL",  # Reserve balances
            # Yield curve (nominal)
            "DGS1MO": "DGS1MO",
            "DGS3MO": "DGS3MO",
            "DGS6MO": "DGS6MO",
            "DGS1": "DGS1",
            "DGS2": "DGS2",
            "DGS3": "DGS3",
            "DGS5": "DGS5",
            "DGS7": "DGS7",
            "DGS10": "DGS10",
            "DGS20": "DGS20",
            "DGS30": "DGS30",
            # SOFR / IORB
            "SOFR": "SOFR",
            "IORB": "IORB",
            # Fed balance sheet (WALCL is total assets, millions of USD)
            "WALCL": "WALCL",
        }
    )

    # --- U.S. Treasury yield curve ---
    TREASURY_URL: str = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/daily-treasury-rates.csv/all/2026"
    )

    # --- Fed H.4.1 release ---
    FED_H41_URL: str = "https://www.federalreserve.gov/releases/h41/current/"

    # --- CBOE data ---
    CBOE_VIX_URL: str = "https://www.cboe.com/us/indices/market_data/download_csv/?filename=vixcurrent.csv"

    def validate(self):
        """Check required config is present."""
        if not self.fred_api_key:
            raise ValueError(
                "FRED_API_KEY is missing. Get one at "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
                " and add it to .env"
            )


config = Config()
