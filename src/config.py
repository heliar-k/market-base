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
    SymbolConfig(
        name="BRK.B", exchange="SMART", ibkr_symbol="BRK B", yf_ticker="BRK-B"
    ),
    SymbolConfig(name="TSLA", exchange="SMART"),
    SymbolConfig(name="MSFT", exchange="SMART"),
    SymbolConfig(name="MCD", exchange="SMART"),
    SymbolConfig(name="LLY", exchange="SMART"),
    SymbolConfig(name="UNH", exchange="SMART"),
    SymbolConfig(name="ISRG", exchange="NASDAQ"),
    SymbolConfig(name="JNJ", exchange="NYSE"),
    SymbolConfig(name="ABBV", exchange="NYSE"),
    SymbolConfig(name="AZN", exchange="NASDAQ"),
    SymbolConfig(name="KO", exchange="SMART"),
    SymbolConfig(name="WMT", exchange="NYSE"),
    SymbolConfig(name="COST", exchange="NASDAQ"),
    SymbolConfig(name="HD", exchange="NYSE"),
    SymbolConfig(name="PG", exchange="NYSE"),
    SymbolConfig(name="PEP", exchange="NASDAQ"),
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
    SymbolConfig(name="AMD", exchange="SMART"),
    SymbolConfig(name="AVGO", exchange="SMART"),
    SymbolConfig(name="INTC", exchange="SMART"),
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
    SymbolConfig(name="SPX", exchange="CBOE", yf_ticker="^GSPC"),
    SymbolConfig(name="IXIC", exchange="NASDAQ", ibkr_symbol="COMP", yf_ticker="^IXIC"),
    SymbolConfig(name="RUT", exchange="RUSSELL", yf_ticker="^RUT"),
    SymbolConfig(name="SOX", exchange="PHLX", yf_ticker="^SOX"),
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
        "DFF": "DFF",  # 有效联邦基金利率（日频，fed-funds 页用；FEDFUNDS 为月频均值）
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
        "JOLTS_QUITS": "JTSQUL",  # 离职人数（千人）；JTSQUR 是离职率（%），勿用
        "UNEMPLOY": "UNEMPLOY",
        "ECI_WAGES": "ECIWAG",
    },
    "producer_prices": {
        "PPI_FD": "PPIFIS",
        "CORE_PPI": "PPICOR",
        "PPI_GOODS": "WPSFD4111",
        "PPI_SERVICES": "WPSFD4211",
    },
    "tic": {
        # TIC 报告（月度，滞后 2 月；FRED 转发 TIC 官方数据）
        # 持仓系列用 FORTREASPOS*（LT+ST 总额，与 Table 5 口径一致，
        # 勿用 FORLTTREASPOS* LT-only——中国持仓将永远低于 7000 亿阈值）
        # 净买入/持仓单位均为百万美元
        "TIC_NET_TOTAL": "FORTREASNET99996",  # 外国净买入美债（LT+ST，总额）
        "TIC_NET_OFFICIAL": "FORTREASNET99990",  # 外国官方净买入（LT+ST）
        "TIC_HOLD_OFFICIAL": "FORTREASPOS99990",  # 外国官方持仓（LT+ST）
        "TIC_HOLD_TOTAL": "FORTREASPOS99996",  # 外国持仓总额（LT+ST，官方占比分子用）
        "TIC_HOLD_JAPAN": "FORTREASPOS42609",  # 日本持仓（Table 5 口径）
        "TIC_HOLD_CHINA": "FORTREASPOS41408",  # 中国持仓（Table 5 口径）
        "TIC_HOLD_SAUDI": "FORTREASPOS45608",  # 沙特持仓（Table 5 口径）
        "TIC_HOLD_UAE": "FORTREASPOS46604",  # 阿联酋持仓（Table 5 口径）
        "TIC_HOLD_UK": "FORTREASPOS13005",  # 英国持仓
        "TIC_HOLD_FRANCE": "FORTREASPOS10804",  # 法国持仓
        "TIC_HOLD_BELGIUM": "FORTREASPOS10251",  # 比利时持仓
        "TIC_HOLD_IRELAND": "FORTREASPOS11401",  # 爱尔兰持仓
        "TIC_HOLD_LUXEMBOURG": "FORTREASPOS11703",  # 卢森堡持仓
        "TIC_HOLD_SWISS": "FORTREASPOS12688",  # 瑞士持仓
        # 海外官方占比（D.3，<23% 结构性偏空）分母 = data/treasury/mspd.csv 的
        # TOTAL_DEBT（总未偿债务，月度）。勿用 GFDEBTN——FRED 侧已停更数月。
    },
}

FRED_SERIES_FLAT = {
    metric: sid for category in FRED_SERIES.values() for metric, sid in category.items()
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

# ── 期限结构分类：哪些 FRED 分类有收益率曲线 + 期限顺序（短→长）──
# 由 TERM_INFO 派生（DGS* = rates 名义收益率，DFII* = tips 实际收益率），勿另起列表。
TERM_SERIES = {
    "rates": [s for s in TERM_INFO if s.startswith("DGS")],
    "tips": [s for s in TERM_INFO if s.startswith("DFII")],
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
    "TDEX": "^TDEX",  # Nations TailDex 尾部风险指数（timsun 波动率面板）
    "VOLI": "^VOLI",  # Nations VolDex 波动率指数（timsun 波动率面板）
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "WTI": "CL=F",
    "NG": "NG=F",  # 天然气（timsun 商品板块）
    "Brent": "BZ=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "Copper": "HG=F",
    "TLT": "TLT",
    "IEF": "IEF",  # 7-10 年国债 ETF
    "HYG": "HYG",
    "LQD": "LQD",
    "KBWB": "KBWB",  # KBW 银行 ETF（信用页银行系统风险代理）
    "SMH": "SMH",  # 半导体 ETF（timsun ETF 看板）
    "SOXX": "SOXX",  # 半导体 ETF（timsun ETF 看板）
    "SOX": "^SOX",
    "N225": "^N225",
    "KOSPI": "^KS11",
    "NIFTY": "^NSEI",
    "SSE": "000001.SS",
    "SZSE": "399001.SZ",
    "USDJPY": "JPY=X",  # 1 美元兑日元
    "USDCNY": "CNY=X",  # 1 美元兑人民币
    "EURUSD": "EURUSD=X",  # 欧元/美元
    "GBPUSD": "GBPUSD=X",  # 英镑/美元
    "USDKRW": "KRW=X",  # 美元/韩元
    # 韩股/中概等纯数字代码用可读名（SAMSUNG/SKHYNIX），不直接用 KRX 数字代码
    "SAMSUNG": "005930.KS",
    "SKHYNIX": "000660.KS",
}


# ── 外汇对（timsun /assets/fx 面板；独立管线 data/fx/fx_pairs.csv）──
# key: (yf_ticker, 中文名, 分组)；USD pressure = 该对涨跌统一换算成“美元强弱”
FX_PAIRS: dict[str, tuple[str, str, str]] = {
    "DXY": ("DX-Y.NYB", "美元指数", "美元锚"),
    "EURUSD": ("EURUSD=X", "欧元/美元", "G10"),
    "GBPUSD": ("GBPUSD=X", "英镑/美元", "G10"),
    "USDJPY": ("JPY=X", "美元/日元", "G10"),
    "USDCHF": ("CHF=X", "美元/瑞郎", "G10"),
    "AUDUSD": ("AUDUSD=X", "澳元/美元", "商品货币"),
    "NZDUSD": ("NZDUSD=X", "纽元/美元", "商品货币"),
    "USDCAD": ("CAD=X", "美元/加元", "商品货币"),
    "USDCNH": ("CNH=X", "美元/离岸人民币", "亚洲/EM"),
    "USDHKD": ("HKD=X", "美元/港币", "亚洲/EM"),
    "USDKRW": ("KRW=X", "美元/韩元", "亚洲/EM"),
    "USDINR": ("INR=X", "美元/卢比", "亚洲/EM"),
    "USDMXN": ("MXN=X", "美元/墨西哥比索", "高贝塔EM"),
    "USDBRL": ("BRL=X", "美元/雷亚尔", "高贝塔EM"),
    "USDZAR": ("ZAR=X", "美元/兰特", "高贝塔EM"),
    "EURJPY": ("EURJPY=X", "欧元/日元", "交叉盘"),
}

# ── ETF 精选池（timsun /assets/etfs 研究池 25 只；独立管线 data/etf/）──
# ticker → (中文名, 分类)；分类与 timsun ETF 页筛选器一致
ETF_POOL: dict[str, tuple[str, str]] = {
    "SMH": ("半导体核心", "ai_semis"),
    "SOXX": ("半导体核心", "ai_semis"),
    "PSI": ("动量半导体", "ai_semis"),
    "XSD": ("等权半导体", "ai_semis"),
    "IGV": ("软件", "ai_software"),
    "SKYY": ("云计算", "ai_software"),
    "SPY": ("宽基美股", "broad"),
    "QQQ": ("纳斯达克100", "broad"),
    "IWM": ("小盘股", "broad"),
    "LQD": ("投资级信用", "bond"),
    "HYG": ("高收益信用", "bond"),
    "TLT": ("长债", "bond"),
    "GLD": ("黄金", "commodity"),
    "SLV": ("白银", "commodity"),
    "USO": ("原油", "commodity"),
    "EEM": ("新兴市场", "global"),
    "FXI": ("中国大盘", "global"),
    "KWEB": ("中国互联网", "global"),
    "CQQQ": ("中国科技", "global"),
    "EWT": ("台湾科技链", "global"),
    "AIQ": ("AI 主题", "theme"),
    "BOTZ": ("机器人/AI", "theme"),
    "ROBO": ("机器人自动化", "theme"),
    "ARKW": ("互联网主题", "theme"),
    "ARKK": ("高 beta 主题", "high_beta"),
}

# ETF 全量清单名称关键词 → 分类（Nasdaq Trader 清单 + 规则分类，timsun 口径简化版）
ETF_KEYWORDS: dict[str, list[str]] = {
    "bond": [
        "bond",
        "treasury",
        "municipal",
        "corp",
        "credit",
        "float",
        "investment grade",
        "high yield",
        "agg",
    ],
    "commodity": [
        "gold",
        "silver",
        "oil",
        "energy",
        "commodity",
        "copper",
        "miners",
        "natural gas",
        "precious",
        "uranium",
        "agriculture",
    ],
    "crypto": ["bitcoin", "ethereum", "crypto", "blockchain", "bito"],
    "currency": ["currency", "dollar", "euro", "yen", "fx", "futures"],
    "factor_income": [
        "dividend",
        "income",
        "value",
        "momentum",
        "quality",
        "low volatility",
        "equal weight",
    ],
    "global": [
        "international",
        "emerging",
        "china",
        "europe",
        "asia",
        "india",
        "japan",
        "developed",
        "ex-us",
        "world",
    ],
    "leveraged_inverse": ["2x", "3x", "inverse", "ultrashort", "ultrapro"],
    "high_beta": ["growth", "innovation", "disrupt"],
    "theme": [
        "ai",
        "semiconductor",
        "cyber",
        "clean",
        "water",
        "biotech",
        "robotics",
        "cloud",
        "space",
    ],
    "broad": [],
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
