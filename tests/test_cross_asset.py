"""cross_asset 相关性矩阵测试：±1 边界 / 无关序列 / 真实快照跑通。"""

import numpy as np
import pandas as pd

from src.cross_asset import compute_correlation_matrix

N = 60
K = np.arange(N)
DRIFT = 0.01 + 0.001 * np.sin(K / 3)  # 每日收益基准（带微小波动，避免零方差）


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SPX": 100 * np.cumprod(1 + DRIFT),
            "TLT": 100 * np.cumprod(1 + DRIFT),  # 与 SPX 完全正相关
            "WTI": 100 * np.cumprod(1 - DRIFT),  # 与 SPX 完全负相关
            "BTC": 100 * (1 + 0.02 * np.sin(K / 3)),  # 无关序列
        },
        index=pd.date_range("2026-01-01", periods=N),
    )


def test_perfect_correlation_bounds():
    m, a = compute_correlation_matrix(_prices())
    assert abs(m.loc["SPX", "TLT"] - 1.0) < 1e-6
    assert abs(m.loc["WTI", "SPX"] + 1.0) < 1e-6
    assert abs(a["SPX_TLT_30d"] - 1.0) < 1e-6
    assert abs(a["WTI_SPX_30d"] + 1.0) < 1e-6


def test_unrelated_series_near_zero():
    m, _ = compute_correlation_matrix(_prices())
    assert abs(m.loc["BTC", "SPX"]) < 0.3


def test_short_history_returns_nan_matrix():
    # 历史不足 window → 矩阵全 NaN（调用方需自行判断）
    short = _prices().iloc[:10]
    m, _ = compute_correlation_matrix(short, window=30)
    assert m.isna().all().all()


def test_trailing_short_column_computes_with_min_periods():
    # 扩展列（如 ETH）只有尾部 13 个观测：min_periods 允许窗口内 NaN，
    # 不应整列空白（回归：rolling(30) 默认窗口内任一 NaN 即 NaN）
    df = _prices()
    df["ETH"] = df["SPX"]  # 与 SPX 完全正相关
    df.loc[df.index[:-13], "ETH"] = np.nan
    m, _ = compute_correlation_matrix(df)
    assert abs(m.loc["ETH", "SPX"] - 1.0) < 1e-6
    assert abs(m.loc["ETH", "ETH"] - 1.0) < 1e-6
