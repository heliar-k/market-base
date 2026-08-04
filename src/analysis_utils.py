"""分析模块共享小工具：CSV 读取 + 时序取值/变化守卫（credit/rates/volatility 共用）。

口径注意：分位函数刻意不收敛——credit_analysis._pct 用严格 <（FRED OAS 整 bp
并列值多，<= 会虚高分位），volatility_analysis._percentile 用 <=（VIX 连续值），
语义不同，合并会改变行为。
"""

from pathlib import Path

import pandas as pd


def read_csv_or_empty(path: Path, index_col: str = "date") -> pd.DataFrame:
    """读时间序列 CSV；文件缺失返回空 DataFrame。"""
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, index_col=index_col, parse_dates=True)


def latest(s: pd.Series) -> float | None:
    """最后一个非 NaN 值；空序列返回 None。"""
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def chg_prev(s: pd.Series, n: int) -> tuple[float, float] | None:
    """最近值与 n 个交易日前值；样本不足或前值缺失/为零返回 None。

    变化类指标的公共守卫：统一判空条件，避免各副本各自实现导致行为漂移。
    """
    s = s.dropna()
    if len(s) < n + 1:
        return None
    cur, prev = s.iloc[-1], s.iloc[-1 - n]
    if pd.isna(prev) or prev == 0:
        return None  # 缺失或零基数（相对变化会除零）
    return float(cur), float(prev)


def chg_pct(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 个交易日前的变化 %。"""
    pair = chg_prev(s, n)
    return None if pair is None else round((pair[0] / pair[1] - 1) * 100, 2)


def zone(v: float, zones: list[tuple[str, float, float, str]]) -> tuple[str, str]:
    """按 (label, lo, hi, color) 区间表查 v 所在区间；表外回落最后一档。"""
    for label, lo, hi, color in zones:
        if lo <= v < hi:
            return label, color
    return zones[-1][0], zones[-1][3]
