"""期权定价共享模块（C4 接缝）

compute_gex / sell_put / hedge_planner 三处重复的收敛点：
  1. fetch_yf_chain  — yfinance 期权链统一拉取（行规整）
  2. aggregate_wall  — 按行权价聚合期权墙（GEX 分布）

惯例：gamma call 正 / put 负；gex 单位美元。
"""

import logging
from math import erf, exp, pi, sqrt
from math import log as _ln

import pandas as pd

log = logging.getLogger("pricing")

# BS 无风险利率（与 compute_gex.greeks_from_yf / options_structure 一致）
R = 0.04


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2)))


def _norm_pdf(x: float) -> float:
    return exp(-x * x / 2) / sqrt(2 * pi)


def d1_from(S: float, K: float, T: float, sigma: float, r: float = R) -> float:
    """BS d1（vanna ≈ −γ·S·√T·d1 入参）。T≤0 或 σ≤0 返回 0。"""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (_ln(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrt(T))


def bs_greeks(
    S: float, K: float, T: float, sigma: float, r: float = R
) -> tuple[float, float]:
    """(gamma, delta_call) per share；put delta = delta_call − 1，调用方处理。"""
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0
    sig_t = sigma * sqrt(T)
    d1 = (_ln(S / K) + (r + sigma * sigma / 2) * T) / sig_t
    gamma = _norm_pdf(d1) / (S * sig_t)
    return gamma, _norm_cdf(d1)


# 统一列（三调用方各自消费子集）
CHAIN_COLUMNS = [
    "expiration",
    "strike",
    "right",
    "bid",
    "ask",
    "last",
    "openInterest",
    "impliedVolatility",
    "volume",
]


def fetch_yf_chain(symbol: str, expirations: list[str]) -> pd.DataFrame:
    """拉取 yfinance 期权链并规整为统一行结构。

    - expiration 统一 %Y%m%d（输入兼容带横线格式）
    - 唯一规整：strike 转 float、openInterest NaN → 0
    - bid/ask/last/IV/volume 原样保留（NaN 兜底是消费方策略）
    - 单个到期日失败跳过，不影响其他到期日
    """
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    log.info(f"从 yfinance 拉取 {symbol} 期权链（{len(expirations)} 个到期日）...")
    ticker = yf.Ticker(symbol)
    results = []
    for exp_dt in expirations:
        yf_date = exp_dt.replace("-", "")
        exp_fmt = f"{yf_date[:4]}-{yf_date[4:6]}-{yf_date[6:8]}"
        try:
            chain = ticker.option_chain(exp_fmt)
            for right, df in [("C", chain.calls), ("P", chain.puts)]:
                for _, row in df.iterrows():
                    oi = row.get("openInterest", 0)
                    results.append(
                        {
                            "expiration": yf_date,
                            "strike": float(row["strike"]),
                            "right": right,
                            "bid": row.get("bid", None),
                            "ask": row.get("ask", None),
                            "last": row.get("lastPrice", None),
                            "openInterest": int(oi) if pd.notna(oi) else 0,
                            "impliedVolatility": row.get("impliedVolatility", None),
                            "volume": row.get("volume", None),
                        }
                    )
        except Exception as e:
            log.warning(f"  yfinance {exp_fmt} 失败，跳过: {e}")

    df = pd.DataFrame(results, columns=CHAIN_COLUMNS)
    log.info(f"yfinance 返回 {len(df)} 条")
    return df


def aggregate_wall(contracts: pd.DataFrame) -> pd.DataFrame:
    """按行权价聚合期权墙。

    输入列：strike / right / gex / openInterest / iv（iv 可为 NaN）
    输出列：call_gex / put_gex / total_gex / total_oi / avg_iv / abs_gex
    """
    wall = (
        contracts.groupby("strike")
        .agg(
            call_gex=(
                "gex",
                lambda x: x[contracts.loc[x.index, "right"] == "C"].sum(),
            ),
            put_gex=(
                "gex",
                lambda x: x[contracts.loc[x.index, "right"] == "P"].sum(),
            ),
            total_gex=("gex", "sum"),
            total_oi=("openInterest", "sum"),
            avg_iv=("iv", "mean"),
        )
        .reset_index()
    )
    wall["abs_gex"] = wall["total_gex"].abs()
    return wall
