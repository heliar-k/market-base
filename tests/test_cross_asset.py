"""cross_asset 相关性矩阵测试：±1 边界 / 无关序列 / 真实快照跑通。"""

import numpy as np
import pandas as pd

from src.cross_asset import (
    ALERTS,
    ASSETS,
    GROUP_LABELS,
    TICKERS,
    compute_correlation_matrix,
)

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


# ── 22 标的 + 4 报警对（关联分析面板数据流）────────────────────


def test_full_ticker_set_alerts_populated():
    """22 标的全量输入：矩阵形状 + 4 个报警对标量齐全（DXY_HYG/MOVE_SPX 回归）。"""
    rng = np.random.default_rng(7)
    n = 60
    base = rng.normal(0, 0.01, n)
    df = pd.DataFrame(
        {
            t: 100 * np.cumprod(1 + base * (1 if i % 2 else -1))
            for i, t in enumerate(TICKERS)
        },
        index=pd.date_range("2026-01-01", periods=n),
    )
    m, a = compute_correlation_matrix(df)
    assert list(m.columns) == list(TICKERS)
    assert len(a) == 4
    assert set(a) == {"SPX_TLT_30d", "WTI_SPX_30d", "DXY_HYG_30d", "MOVE_SPX_30d"}
    for v in a.values():
        assert -1 <= v <= 1


def test_alerts_labels_match_keys():
    """每个报警对都有中文标签（server payload 直接消费）。"""
    assert all(len(label) > 0 for _i, _j, _key, label in ALERTS)
    assert {key for _i, _j, key, _label in ALERTS} == {
        "SPX_TLT_30d",
        "WTI_SPX_30d",
        "DXY_HYG_30d",
        "MOVE_SPX_30d",
    }


def test_assets_metadata_covers_all_tickers():
    """矩阵标的与分组元数据一一对应（热力图分组建模依赖）。"""
    assert set(ASSETS) == set(TICKERS)
    assert set(GROUP_LABELS) == {a["group"] for a in ASSETS.values()}
