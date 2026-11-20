"""期权定价共享模块（C4 接缝）

compute_gex / sell_put / hedge_planner 三处重复的收敛点：
  1. fetch_yf_chain  — yfinance 期权链统一拉取（行规整）
  2. aggregate_wall  — 按行权价聚合期权墙（GEX 分布）

惯例：gamma call 正 / put 负；gex 单位美元。
"""

import logging
from datetime import datetime
from math import erf, exp, pi, sqrt
from math import log as _ln

import numpy as np
import pandas as pd

from src.clock import ET

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


def bs_greeks(S: float, K: float, T: float, sigma: float, r: float = R) -> tuple[float, float]:
    """(gamma, delta_call) per share；put delta = delta_call − 1，调用方处理。"""
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0
    sig_t = sigma * sqrt(T)
    d1 = (_ln(S / K) + (r + sigma * sigma / 2) * T) / sig_t
    gamma = _norm_pdf(d1) / (S * sig_t)
    return gamma, _norm_cdf(d1)


def years_to_expiration(expiration: str, now: datetime) -> float:
    """股票/ETF 期权到美东到期日 16:00 的剩余年数；已到期返回 0。"""
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    expiry = datetime.strptime(expiration, "%Y%m%d").replace(
        hour=16, minute=0, second=0, microsecond=0, tzinfo=ET
    )
    seconds = (expiry - now.astimezone(ET)).total_seconds()
    return max(seconds, 0.0) / (365 * 24 * 3600)


def net_gex_profile(
    strikes,
    rights,
    years,
    ivs,
    ois,
    prices,
    r: float = R,
    multiplier: float = 100.0,
) -> np.ndarray:
    """按假设标的价格序列计算全链净 GEX（美元，Call 正 / Put 负）。

    单合约 GEX(S) = sign · γ(S) · OI · M · S = sign · OI · M · φ(d1(S)) / (σ√T)。
    """
    price_arr = np.asarray(prices, dtype=float)
    if price_arr.size == 0:
        return np.zeros(0, dtype=float)
    k_arr = np.asarray(strikes, dtype=float)
    r_arr = np.asarray(rights)
    t_arr = np.asarray(years, dtype=float)
    iv_arr = np.asarray(ivs, dtype=float)
    oi_arr = np.asarray(ois, dtype=float)
    valid = (
        np.isfinite(k_arr)
        & np.isfinite(t_arr)
        & np.isfinite(iv_arr)
        & np.isfinite(oi_arr)
        & (k_arr > 0)
        & (t_arr > 0)
        & (iv_arr > 0)
        & (oi_arr > 0)
    )
    if not np.any(valid):
        return np.zeros(price_arr.shape, dtype=float)
    k = k_arr[valid][None, :]
    t = t_arr[valid][None, :]
    iv = iv_arr[valid][None, :]
    oi = oi_arr[valid][None, :]
    sign = np.where(r_arr[valid] == "C", 1.0, -1.0)[None, :]
    sig_t = iv * np.sqrt(t)
    s = np.maximum(price_arr, 1e-12)[:, None]
    d1 = (np.log(s / k) + (r + 0.5 * iv * iv) * t) / sig_t
    phi = np.exp(-0.5 * d1 * d1) / sqrt(2.0 * pi)
    return (sign * oi * multiplier * (phi / sig_t)).sum(axis=1)


def _log_gex_diff(
    k_c: np.ndarray,
    t_c: np.ndarray,
    iv_c: np.ndarray,
    oi_c: np.ndarray,
    k_p: np.ndarray,
    t_p: np.ndarray,
    iv_p: np.ndarray,
    oi_p: np.ndarray,
    prices: np.ndarray,
    r: float,
) -> np.ndarray:
    """对数空间求 ln(CallGEX(S)) - ln(|PutGEX(S)|)，严格保号且在高斯深尾部不下溢。"""
    s = np.maximum(prices, 1e-12)[:, None]

    def _side_lse(k: np.ndarray, t: np.ndarray, iv: np.ndarray, oi: np.ndarray) -> np.ndarray:
        sig_t = iv[None, :] * np.sqrt(t[None, :])
        d1 = (np.log(s / k[None, :]) + (r + 0.5 * iv[None, :] * iv[None, :]) * t[None, :]) / sig_t
        exponent = np.log(oi[None, :] / sig_t) - 0.5 * d1 * d1
        m = np.max(exponent, axis=1, keepdims=True)
        return m[:, 0] + np.log(np.sum(np.exp(exponent - m), axis=1))

    return _side_lse(k_c, t_c, iv_c, oi_c) - _side_lse(k_p, t_p, iv_p, oi_p)


def zero_gamma_flip(
    strikes,
    rights,
    years,
    ivs,
    ois,
    spot: float,
    r: float = R,
    band: float = 0.30,
    steps: int = 121,
) -> float | None:
    """扫描标的价格求 Zero Gamma（Net GEX(S) = 0 负转正穿越点），仅在现价 ±band 内搜索。"""
    if spot <= 0 or band <= 0:
        return None
    k_arr = np.asarray(strikes, dtype=float)
    r_arr = np.asarray(rights)
    t_arr = np.asarray(years, dtype=float)
    iv_arr = np.asarray(ivs, dtype=float)
    oi_arr = np.asarray(ois, dtype=float)
    valid = (
        np.isfinite(k_arr)
        & np.isfinite(t_arr)
        & np.isfinite(iv_arr)
        & np.isfinite(oi_arr)
        & (k_arr > 0)
        & (t_arr > 0)
        & (iv_arr > 0)
        & (oi_arr > 0)
    )
    if not np.any(valid):
        return None
    k_v, r_v, t_v, iv_v, oi_v = (
        k_arr[valid],
        r_arr[valid],
        t_arr[valid],
        iv_arr[valid],
        oi_arr[valid],
    )
    is_call = r_v == "C"
    is_put = ~is_call
    # 缺少任意一侧合约时，全链 Net GEX 严格恒正或恒负，不存在 Zero Gamma
    if not np.any(is_call) or not np.any(is_put):
        return None
    k_c, t_c, iv_c, oi_c = k_v[is_call], t_v[is_call], iv_v[is_call], oi_v[is_call]
    k_p, t_p, iv_p, oi_p = k_v[is_put], t_v[is_put], iv_v[is_put], oi_v[is_put]

    lo_bound = max(spot * (1.0 - band), 1e-6)
    hi_bound = spot * (1.0 + band)
    # 合并等距网格、现价以及带内全部行权价，防止近到期窄间距尖峰被网格步长跳过
    in_band_strikes = k_v[(k_v >= lo_bound) & (k_v <= hi_bound)]
    grid = np.unique(
        np.concatenate([np.linspace(lo_bound, hi_bound, max(steps, 3)), [spot], in_band_strikes])
    )
    vals = _log_gex_diff(k_c, t_c, iv_c, oi_c, k_p, t_p, iv_p, oi_p, grid, r)

    flips: list[float] = []
    for i in range(len(grid) - 1):
        if vals[i] < 0.0 <= vals[i + 1]:
            lo, hi = float(grid[i]), float(grid[i + 1])
            for _ in range(16):
                mid = 0.5 * (lo + hi)
                v_mid = float(
                    _log_gex_diff(k_c, t_c, iv_c, oi_c, k_p, t_p, iv_p, oi_p, np.array([mid]), r)[0]
                )
                if v_mid < 0.0:
                    lo = mid
                else:
                    hi = mid
            flips.append(0.5 * (lo + hi))

    if not flips:
        return None
    val_spot = float(
        _log_gex_diff(k_c, t_c, iv_c, oi_c, k_p, t_p, iv_p, oi_p, np.array([spot]), r)[0]
    )
    if val_spot >= 0.0:
        below = [price for price in flips if price <= spot]
        flip = below[-1] if below else None
    else:
        above = [price for price in flips if price > spot]
        flip = above[0] if above else None
    return round(float(flip), 2) if flip is not None else None


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
