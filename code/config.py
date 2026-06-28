"""
统一配置 — 覆盖所有数据源（FRED / Treasury / CBOE / yfinance / IBKR）。
.env 管理密钥，此处定义数据资产与连接参数。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── .env ────────────────────────────────────────────────────────────────────
_env_path = ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")


# ═══════════════════════════════════════════════════════════════════════════════
# 数据资产定义（模块级常量，一目了然）
# ═══════════════════════════════════════════════════════════════════════════════

FRED_SERIES = {
    # ── 波动率 ──
    "VIX": "VIXCLS",
    # ── 信用利差 ──
    "HY_OAS": "BAMLH0A0HYM2",
    "IG_OAS": "BAMLC0A0CM",
    # ── 通胀预期 / BEI ──
    "T10YIE": "T10YIE",
    "T5YIE": "T5YIE",
    "T5YIFR": "T5YIFR",
    # ── TIPS 实际收益率（全期限，用于 BEI 计算）──
    "DFII5": "DFII5",
    "DFII7": "DFII7",
    "DFII10": "DFII10",
    "DFII20": "DFII20",
    "DFII30": "DFII30",
    # ── 金融状况 ──
    "NFCI": "NFCI",
    # ── 流动性 ──
    "RRPONTSYD": "RRPONTSYD",
    "WTREGEN": "WTREGEN",
    "WRESBAL": "WRESBAL",
    # ── 名义国债收益率曲线（全期限）──
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
    # ── 政策利率 ──
    "SOFR": "SOFR",
    "IORB": "IORB",
    # ── 美联储资产负债表 ──
    "WALCL": "WALCL",
}

YF_TICKERS = {
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

IBKR_SYMBOLS = [
    {"name": "SPX", "type": "index", "exchange": "CBOE", "currency": "USD"},
    {
        "name": "IXIC",
        "type": "index",
        "exchange": "NASDAQ",
        "currency": "USD",
        "symbol": "COMP",
    },
    {"name": "VIX", "type": "index", "exchange": "CBOE", "currency": "USD"},
    {"name": "AAPL", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "TSLA", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "MSFT", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "MU", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "TSM", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "RUT", "type": "index", "exchange": "RUSSELL", "currency": "USD"},
    {"name": "SPY", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "QQQ", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "GOOG", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "META", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "AMZN", "type": "stock", "exchange": "SMART", "currency": "USD"},
    {"name": "NVDA", "type": "stock", "exchange": "SMART", "currency": "USD"},
]


# ═══════════════════════════════════════════════════════════════════════════════
# 配置结构
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class IbkrConfig:
    """IBKR 连接与拉取参数。"""

    host: str = "127.0.0.1"
    port: int = 4002
    bar_size: str = "1 day"
    duration: str = "2 Y"
    what_to_show: str = "TRADES"
    use_rth: bool = True
    request_delay_seconds: int = 15
    output_dir: str = "data"
    output_format: str = "csv"
    output_encoding: str = "utf-8"


@dataclass
class Config:
    """顶层配置 — API 密钥 + 数据资产 + 子配置。"""

    # ── 密钥（从 .env 注入）──
    fred_api_key: str = field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))
    https_proxy: str = field(default_factory=lambda: os.getenv("HTTPS_PROXY", ""))
    http_proxy: str = field(default_factory=lambda: os.getenv("HTTP_PROXY", ""))

    # ── 数据资产（引用模块常量，避免重复定义）──
    fred_series: dict = field(default_factory=lambda: dict(FRED_SERIES))
    yf_tickers: dict = field(default_factory=lambda: dict(YF_TICKERS))
    ibkr_symbols: list[dict] = field(default_factory=lambda: list(IBKR_SYMBOLS))

    # ── 子配置 ──
    ibkr: IbkrConfig = field(default_factory=IbkrConfig)

    def validate(self) -> None:
        if not self.fred_api_key:
            raise ValueError(
                "FRED_API_KEY 未配置。请在 .env 中设置，"
                "获取地址: https://fred.stlouisfed.org/docs/api/api_key.html"
            )


config = Config()
