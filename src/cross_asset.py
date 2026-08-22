"""
跨资产相关性矩阵（timsun /assets 面板：30 日滚动相关 + 股债/油股报警）。

输入 data/yfinance/asset_prices.csv（日频快照宽表），输出：
  - data/cross_asset/correlation.csv   最新 30 日相关系数矩阵（覆盖写，行=列=标的）
  - data/cross_asset/alerts.csv        SPX_TLT / WTI_SPX 报警标量（按日追加，可看趋势）

纯派生计算，无网络依赖。由 GitHub Actions 在资产快照更新后调用。
"""

import logging

import pandas as pd

from src.config import ROOT

logger = logging.getLogger(__name__)

# 矩阵标的（timsun 面板同款）：美股/债券/商品/加密/美元
# ETH/NG/EURUSD 为 2026-08 扩展（timsun /assets 主页表格内资产），
# 新增列在 asset_prices.csv 中已存在（YF_TICKERS 同款）
TICKERS = [
    "SPX",
    "NDX",
    "RUT",
    "DJI",
    "TLT",
    "HYG",
    "LQD",
    "Gold",
    "Silver",
    "WTI",
    "Copper",
    "BTC",
    "ETH",
    "NG",
    "DXY",
    "EURUSD",
]
# 扩展列（历史段可能为 NaN，不可用于行级 dropna）
EXTENDED_TICKERS = {"ETH", "NG", "EURUSD"}
# 行过滤只看核心标的（扩展列与核心列相关性由 rolling corr 两两有效对计算）
CORE_TICKERS = [t for t in TICKERS if t not in EXTENDED_TICKERS]

# 报警对：(i, j, 列名)
ALERTS = [("SPX", "TLT", "SPX_TLT_30d"), ("WTI", "SPX", "WTI_SPX_30d")]
WINDOW = 30

MATRIX_PATH = ROOT / "data" / "cross_asset" / "correlation.csv"
ALERTS_PATH = ROOT / "data" / "cross_asset" / "alerts.csv"


def compute_correlation_matrix(
    prices: pd.DataFrame, window: int = WINDOW
) -> tuple[pd.DataFrame, dict[str, float]]:
    """prices：宽表日收盘（index=date，列=标的）→ (最新相关系数矩阵, 报警标量)。

    矩阵行/列按 TICKERS 顺序；标量 = 对应 (i, j) 的 30 日相关系数。
    """
    cols = [c for c in TICKERS if c in prices.columns]
    rets = prices[cols].pct_change()
    corr = rets.rolling(window).corr()  # MultiIndex (date, i) × j
    latest = corr.loc[corr.index.get_level_values(0).max()]

    alerts = {}
    for i, j, name in ALERTS:
        if i in cols and j in cols:
            alerts[name] = round(float(latest.loc[i, j]), 4)
    return latest, alerts


def main() -> None:
    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    prices = pd.read_csv(path, index_col="date", parse_dates=True)
    prices = prices[[c for c in prices.columns if not c.endswith("_volume")]]
    # 周末行部分标的无值（BTC/外汇 7×24 有、股票/债券无）→ 相关性按交易日算。
    # 行过滤只看核心 13 标的（扩展列 ETH/NG/EURUSD 历史段可能为 NaN，拖累全部行）；
    # 扩展列与核心列的相关性由 rolling corr 按两两有效对计算（dropna 不适用于扩展列）。
    core_cols = [c for c in CORE_TICKERS if c in prices.columns]
    prices = prices.dropna(subset=core_cols)
    if len(prices) < WINDOW + 1:
        logger.warning(f"资产快照仅 {len(prices)} 行，不足 {WINDOW + 1} 行，跳过")
        return

    matrix, alerts = compute_correlation_matrix(prices)

    # 矩阵文件带观测日：首行 index="date"（Web 热力图消费时跳过该行）
    matrix = pd.concat(
        [
            pd.DataFrame(
                [[str(prices.index[-1].date())] * len(matrix.columns)],
                index=["date"],
                columns=matrix.columns,
            ),
            matrix,
        ]
    )
    matrix.index.name = "asset"
    matrix_path = MATRIX_PATH
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(matrix_path)
    logger.info(f"相关性矩阵 → {matrix_path} ({matrix.shape[0]}×{matrix.shape[1]})")

    alerts_df = pd.DataFrame([{"date": str(prices.index[-1].date()), **alerts}])
    from .fetchers._io import upsert_rows

    upsert_rows(ALERTS_PATH, alerts_df, subset=["date"], sort_by=["date"])
    logger.info(f"报警标量 → {ALERTS_PATH}: {alerts}")


if __name__ == "__main__":
    main()
