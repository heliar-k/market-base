"""
统一配置 — 覆盖所有数据源（FRED / Treasury / CBOE / yfinance / IBKR）。
.env 管理密钥，此处定义数据资产与连接参数。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

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


@dataclass
class SymbolConfig:
    """单品种 OHLCV 拉取配置。"""

    name: str  # 品种代码，也是数据文件名
    exchange: str  # IBKR 交易所
    currency: str = "USD"
    ibkr_symbol: str | None = None  # IBKR 合约代码（默认同 name）
    yf_ticker: str | None = None  # yfinance 回退 ticker


# ── 股票 ──
STOCKS: list[SymbolConfig] = [
    SymbolConfig(name="AAPL", exchange="SMART"),
    SymbolConfig(name="BRK.B", exchange="SMART", ibkr_symbol="BRK B"),
    SymbolConfig(name="TSLA", exchange="SMART"),
    SymbolConfig(name="MSFT", exchange="SMART"),
    SymbolConfig(name="MCD", exchange="SMART"),
    SymbolConfig(name="LLY", exchange="SMART"),
    SymbolConfig(name="UNH", exchange="SMART"),
    SymbolConfig(name="KO", exchange="SMART"),
    SymbolConfig(name="MU", exchange="SMART"),
    SymbolConfig(name="TSM", exchange="SMART"),
    SymbolConfig(name="SPY", exchange="SMART"),
    SymbolConfig(name="QQQ", exchange="SMART"),
    SymbolConfig(name="GOOG", exchange="SMART"),
    SymbolConfig(name="META", exchange="SMART"),
    SymbolConfig(name="AMZN", exchange="SMART"),
    SymbolConfig(name="NVDA", exchange="SMART"),
    SymbolConfig(name="CRCL", exchange="SMART"),
    SymbolConfig(name="HOOD", exchange="SMART"),
    SymbolConfig(name="COIN", exchange="SMART"),
    SymbolConfig(name="SNDK", exchange="SMART"),
    # ── 算力拥挤度观察（IBKR 不可用时 yfinance 回退）──
    # 韩股/中概等纯数字代码用可读名（SAMSUNG/SKHYNIX），yf_ticker 保留原始代码
    SymbolConfig(
        name="SAMSUNG",
        exchange="KSE",
        currency="KRW",
        ibkr_symbol="005930",
        yf_ticker="005930.KS",
    ),
    SymbolConfig(
        name="SKHYNIX",
        exchange="KSE",
        currency="KRW",
        ibkr_symbol="000660",
        yf_ticker="000660.KS",
    ),
]

# ── 指数 ──
INDICES: list[SymbolConfig] = [
    SymbolConfig(name="SPX", exchange="CBOE"),
    SymbolConfig(name="IXIC", exchange="NASDAQ", ibkr_symbol="COMP"),
    SymbolConfig(name="VIX", exchange="CBOE"),
    SymbolConfig(name="RUT", exchange="RUSSELL"),
    SymbolConfig(name="SOX", exchange="PHLX"),
]


def _to_legacy_dict(sc: SymbolConfig, kind: str) -> dict:
    """将 SymbolConfig 转为 ibkr_fetcher 兼容的 dict 格式。"""
    d: dict = {
        "name": sc.name,
        "type": kind,
        "exchange": sc.exchange,
        "currency": sc.currency,
    }
    if sc.ibkr_symbol:
        d["symbol"] = sc.ibkr_symbol
    if sc.yf_ticker:
        d["yf_ticker"] = sc.yf_ticker
    return d


# 向后兼容：ibkr_fetcher / server / tui 使用的合并列表（自动派生）
IBKR_SYMBOLS: list[dict] = [_to_legacy_dict(s, "stock") for s in STOCKS] + [
    _to_legacy_dict(s, "index") for s in INDICES
]

# {category: {metric: series_id}}
FRED_SERIES = {
    "volatility": {
        "VIX": "VIXCLS",
        "HY_OAS": "BAMLH0A0HYM2",
        "IG_OAS": "BAMLC0A0CM",
    },
    "inflation": {
        "CPI": "CPIAUCSL",
        "PCE": "PCEPI",
        "CORE_CPI": "CPILFESL",
        "CORE_PCE": "PCEPILFE",
        "CPI_SHELTER": "CPIHOSSL",
        "CPI_FOOD": "CPIUFDSL",
        "CPI_ENERGY": "CPIENGSL",
        "CORE_SERVICES": "CUSR0000SASLE",
        "CORE_GOODS": "CUSR0000SACL1E",
        "T5YIE": "T5YIE",
        "T10YIE": "T10YIE",
        "T5YIFR": "T5YIFR",
        "MICH": "MICH",
        "EXPINF_1Y": "EXPINF1YR",
        "EXPINF_2Y": "EXPINF2YR",
        "EXPINF_5Y": "EXPINF5YR",
        "EXPINF_10Y": "EXPINF10YR",
        "SUPERCORE_PCE": "IA001260M",
        "SUPERCORE_PCE_REAL": "LB001260M",
    },
    "labor": {
        "UNRATE": "UNRATE",
        "PAYEMS": "PAYEMS",
        "ICSA": "ICSA",
    },
    "growth": {
        "GDP": "GDPC1",
        "INDPRO": "INDPRO",
        "REAL_PCE": "PCEC96",
        "CAPU": "TCU",
        "DGORDER": "DGORDER",
    },
    "rates": {
        "FEDFUNDS": "FEDFUNDS",
        "DFEDTARL": "DFEDTARL",
        "DFEDTARU": "DFEDTARU",
        "SOFR": "SOFR",
        "SOFR1": "SOFR1",
        "SOFR25": "SOFR25",
        "SOFR75": "SOFR75",
        "SOFR99": "SOFR99",
        "SOFRVOL": "SOFRVOL",
        "OBFR": "OBFR",
        "IORB": "IORB",
        "TGCR": "TGCRRATE",
        "ONRRP": "RRPONTSYAWARD",
        "JP10Y": "IRLTLT01JPM156N",
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
    },
    "tips": {
        "DFII5": "DFII5",
        "DFII7": "DFII7",
        "DFII10": "DFII10",
        "DFII20": "DFII20",
        "DFII30": "DFII30",
    },
    "liquidity": {
        "NFCI": "NFCI",
        "ANFCI": "ANFCI",
        "NFCIRISK": "NFCIRISK",
        "NFCICREDIT": "NFCICREDIT",
        "NFCILEVERAGE": "NFCILEVERAGE",
        "RRPONTSYD": "RRPONTSYD",
        "WTREGEN": "WTREGEN",
        "WRESBAL": "WRESBAL",
        "WRBWFRBL": "WRBWFRBL",
        "WALCL": "WALCL",
        "TREAST": "TREAST",
        "WSHOMCB": "WSHOMCB",
        "SWPT": "SWPT",
    },
    "sentiment": {
        "UMCSENT": "UMCSENT",
        "STLFSI4": "STLFSI4",
    },
    "fx": {
        "DXY": "DTWEXBGS",
    },
    "credit": {
        "AAA": "AAA",
        "BAA": "BAA",
        "AAA10Y": "AAA10Y",
        "BAA10Y": "BAA10Y",
        # 信用利差分层（ICE BofA OAS）
        "BBB_OAS": "BAMLC0A4CBBB",
        "BB_OAS": "BAMLH0A1HYBB",
        "B_OAS": "BAMLH0A2HYB",
        "CCC_OAS": "BAMLH0A3HYC",
        # All-in 融资成本（ICE BofA 有效收益率）
        "IG_YIELD": "BAMLC0A0CMEY",
        "HY_YIELD": "BAMLH0A0HYM2EY",
        # SLOOS 银行信贷标准/需求（季度）
        "SLOOS_CI_STD": "DRTSCILM",
        "SLOOS_CI_DEM": "DRSDCILM",
        "SLOOS_CRE_STD": "DRTSCREL",
        "SLOOS_CC_STD": "DRTSCLCC",
        # 贷款质量（逾期率/核销率，季度）
        "DELINQ_CI": "DRBLACBS",
        "DELINQ_CRE": "DRCRELEXFACBS",
        "DELINQ_CC": "DRCCLACBS",
        "CHGOFF_BUS": "CORBLACBS",
        "CHGOFF_CONS": "CORCACBS",
    },
    "consumption": {
        "PSAVERT": "PSAVERT",
    },
    "labor_market": {
        "JOLTS_OPEN": "JTSJOL",
        "JOLTS_QUITS": "JTSQUR",
        "UNEMPLOY": "UNEMPLOY",
        "ECI_WAGES": "ECIWAG",
    },
    "producer_prices": {
        "PPI_FD": "PPIFIS",
        "CORE_PPI": "PPICOR",
        "PPI_GOODS": "WPSFD4111",
        "PPI_SERVICES": "WPSFD4211",
    },
}

FRED_SERIES_FLAT = {
    metric: sid for category in FRED_SERIES.values() for metric, sid in category.items()
}

# ── 期限结构分类：哪些 FRED 分类有收益率曲线 + 期限顺序（短→长）──
TERM_SERIES = {
    "rates": [
        "DGS1MO",
        "DGS3MO",
        "DGS6MO",
        "DGS1",
        "DGS2",
        "DGS3",
        "DGS5",
        "DGS7",
        "DGS10",
        "DGS20",
        "DGS30",
    ],
    "tips": ["DFII5", "DFII7", "DFII10", "DFII20", "DFII30"],
}


# 期限品种展示信息（FRED 系列名 → 长名 + 短标签），server / TUI 共用，勿另起映射。
# 长名供宏观快照/下拉列表（如“10年期国债收益率”），短标签供期限结构 x 轴（如“10y”）。
class TermInfo(NamedTuple):
    name: str  # 长名（中文描述）
    short: str  # 短标签（期限结构 x 轴）


TERM_INFO: dict[str, TermInfo] = {
    "DGS1MO": TermInfo("1月期国债收益率", "1mo"),
    "DGS3MO": TermInfo("3月期国债收益率", "3mo"),
    "DGS6MO": TermInfo("6月期国债收益率", "6mo"),
    "DGS1": TermInfo("1年期国债收益率", "1y"),
    "DGS2": TermInfo("2年期国债收益率", "2y"),
    "DGS3": TermInfo("3年期国债收益率", "3y"),
    "DGS5": TermInfo("5年期国债收益率", "5y"),
    "DGS7": TermInfo("7年期国债收益率", "7y"),
    "DGS10": TermInfo("10年期国债收益率", "10y"),
    "DGS20": TermInfo("20年期国债收益率", "20y"),
    "DGS30": TermInfo("30年期国债收益率", "30y"),
    "DFII5": TermInfo("5年期TIPS收益率", "5y"),
    "DFII7": TermInfo("7年期TIPS收益率", "7y"),
    "DFII10": TermInfo("10年期TIPS收益率", "10y"),
    "DFII20": TermInfo("20年期TIPS收益率", "20y"),
    "DFII30": TermInfo("30年期TIPS收益率", "30y"),
}

# 当前 FOMC 目标区间兜底值（本地 FRED 数据缺失时用于 ZQ 概率计算）。
FED_TARGET_RANGE_FALLBACK: tuple[float, float] = (3.50, 3.75)

# ── FOMC 会议日历（每年 8 次，联邦储备委员会公布）──


class FomcMeeting(NamedTuple):
    year: int
    month: int
    start_day: int
    end_day: int


FOMC_MEETINGS: list[FomcMeeting] = [
    FomcMeeting(2025, 1, 28, 29),
    FomcMeeting(2025, 3, 18, 19),
    FomcMeeting(2025, 5, 6, 7),
    FomcMeeting(2025, 6, 17, 18),
    FomcMeeting(2025, 7, 29, 30),
    FomcMeeting(2025, 9, 16, 17),
    FomcMeeting(2025, 10, 28, 29),
    FomcMeeting(2025, 12, 9, 10),
    FomcMeeting(2026, 1, 27, 28),
    FomcMeeting(2026, 3, 17, 18),
    FomcMeeting(2026, 4, 28, 29),
    FomcMeeting(2026, 6, 16, 17),
    FomcMeeting(2026, 7, 28, 29),
    FomcMeeting(2026, 9, 15, 16),
    FomcMeeting(2026, 10, 27, 28),
    FomcMeeting(2026, 12, 8, 9),
]

# ── 商品期货（IBKR 拉取）──
COMMODITY_FUTURES = {
    "GC": ("Gold", "COMEX"),
    "CL": ("WTI", "NYMEX"),
    "NG": ("NatGas", "NYMEX"),
    "SI": ("Silver", "COMEX"),
    "HG": ("Copper", "COMEX"),
    "ES": ("SPX", "CME"),
    "NQ": ("Nasdaq", "CME"),
    "YM": ("Dow", "CBOT"),
    "RTY": ("Russell", "CME"),
    "ZQ": ("FedFunds", "CBOT"),
}

# ── yfinance 单品价格快照（独立于 OHLCV 管线）──
YF_TICKERS = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "RUT": "^RUT",
    "DJI": "^DJI",
    "DXY": "DX-Y.NYB",
    "MOVE": "^MOVE",  # 美林国债期权波动率指数（债市 VIX）
    "BTC": "BTC-USD",
    "WTI": "CL=F",
    "Brent": "BZ=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "Copper": "HG=F",
    "TLT": "TLT",
    "HYG": "HYG",
    "LQD": "LQD",
    "KBWB": "KBWB",  # KBW 银行 ETF（信用页银行系统风险代理）
    "SOX": "^SOX",
    "N225": "^N225",
    "KOSPI": "^KS11",
    "NIFTY": "^NSEI",
    "SSE": "000001.SS",
    "SZSE": "399001.SZ",
    "USDJPY": "JPY=X",  # 1 美元兑日元
    "USDCNY": "CNY=X",  # 1 美元兑人民币
    # 韩股/中概等纯数字代码用可读名（SAMSUNG/SKHYNIX），不直接用 KRX 数字代码
    "SAMSUNG": "005930.KS",
    "SKHYNIX": "000660.KS",
}


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
    commodity_futures: dict = field(default_factory=lambda: dict(COMMODITY_FUTURES))

    # ── 子配置 ──
    ibkr: IbkrConfig = field(default_factory=IbkrConfig)

    def validate(self) -> None:
        if not self.fred_api_key:
            raise ValueError(
                "FRED_API_KEY 未配置。请在 .env 中设置，"
                "获取地址: https://fred.stlouisfed.org/docs/api/api_key.html"
            )


config = Config()
