"""
跨资产相关性面板数据（timsun /assets 面板升级版：30 日滚动相关 + 结构报警）。

输入 data/yfinance/asset_prices.csv（日频快照宽表），输出：
  - data/cross_asset/correlation.csv   最新 30 日相关系数矩阵（覆盖写，首行 header 块）
  - data/cross_asset/alerts.csv        结构报警对日序列（按日追加，可看趋势）

纯派生计算，无网络依赖。由 GitHub Actions 在资产快照更新后调用。

报警对选型依据（文献与实践共识，供后续调整）：
  - SPX×TLT   股债相关性：通胀/利率驱动期转正（60/40 失效信号），
              增长驱动期为负（Robeco / CFA Institute 2025 股债驱动因子研究）
  - WTI×SPX   油股相关性：深度负值常伴随滞胀/供给冲击，正值对应需求扩张
  - DXY×HYG   美元-信用：美元走强 + 信用利差走阔 → 金融条件收紧
  - MOVE×SPX  债市波动-股票：同向 = 波动跨资产传染，久期对冲失效
"""

import logging

import pandas as pd

from src.config import ROOT

logger = logging.getLogger(__name__)

# 矩阵标的（22 个，按 ASSETS 分组）：美股/债券/商品/加密/FX/波动率
# ETH/NG/EURUSD 为 2026-08 扩展（timsun /assets 主页表格内资产），
# MOVE/KBWB/SOX/IEF/USDJPY/Brent 为 2026-09 关联分析面板扩展（asset_prices.csv 已有列）
TICKERS = [
    "SPX",
    "NDX",
    "RUT",
    "DJI",
    "SOX",
    "TLT",
    "IEF",
    "HYG",
    "LQD",
    "KBWB",
    "Gold",
    "Silver",
    "WTI",
    "Brent",
    "Copper",
    "NG",
    "BTC",
    "ETH",
    "DXY",
    "USDJPY",
    "EURUSD",
    "MOVE",
]

# 分组元数据（热力图行组着色 + drill-down 标签；顺序即展示顺序）
ASSETS = {
    "SPX": {"group": "equity", "label": "标普500"},
    "NDX": {"group": "equity", "label": "纳指100"},
    "RUT": {"group": "equity", "label": "罗素2000"},
    "DJI": {"group": "equity", "label": "道指"},
    "SOX": {"group": "equity", "label": "费城半导体"},
    "TLT": {"group": "bond", "label": "长期国债"},
    "IEF": {"group": "bond", "label": "7-10年国债"},
    "HYG": {"group": "credit", "label": "高收益债"},
    "LQD": {"group": "credit", "label": "投资级债"},
    "KBWB": {"group": "credit", "label": "银行股ETF"},
    "Gold": {"group": "commodity", "label": "黄金"},
    "Silver": {"group": "commodity", "label": "白银"},
    "WTI": {"group": "commodity", "label": "WTI原油"},
    "Brent": {"group": "commodity", "label": "布伦特原油"},
    "Copper": {"group": "commodity", "label": "铜"},
    "NG": {"group": "commodity", "label": "天然气"},
    "BTC": {"group": "crypto", "label": "比特币"},
    "ETH": {"group": "crypto", "label": "以太坊"},
    "DXY": {"group": "fx", "label": "美元指数"},
    "USDJPY": {"group": "fx", "label": "美元/日元"},
    "EURUSD": {"group": "fx", "label": "欧元/美元"},
    "MOVE": {"group": "vol", "label": "MOVE债市波动"},
}
GROUP_LABELS = {
    "equity": "美股",
    "bond": "国债",
    "credit": "信用",
    "commodity": "商品",
    "crypto": "加密",
    "fx": "美元/FX",
    "vol": "波动率",
}

# 扩展列（历史段可能为 NaN，不可用于行级 dropna）
EXTENDED_TICKERS = {"ETH", "NG", "EURUSD"}
# 行过滤只看核心标的（扩展列与核心列相关性由 rolling corr 两两有效对计算）
CORE_TICKERS = [t for t in TICKERS if t not in EXTENDED_TICKERS]

# 报警对：(i, j, 列名, 中文标签) — 结构性关系，转号/极值即面板叙事素材
ALERTS = [
    ("SPX", "TLT", "SPX_TLT_30d", "股债相关性（60/40 分散化有效性）"),
    ("WTI", "SPX", "WTI_SPX_30d", "油股相关性（滞胀/需求驱动判别）"),
    ("DXY", "HYG", "DXY_HYG_30d", "美元-信用（金融条件收紧信号）"),
    ("MOVE", "SPX", "MOVE_SPX_30d", "债市波动-股票（久期对冲有效性）"),
]
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
    # min_periods=10：扩展列（ETH/NG/EURUSD，历史不足 30 日）按窗口内有效观测计算；
    # 默认为 window 时窗口内任一 NaN（含 pct_change 首值 NaN）即整格空白。
    corr = rets.rolling(window, min_periods=10).corr()  # MultiIndex (date, i) × j
    latest = corr.loc[corr.index.get_level_values(0).max()]

    alerts = {}
    for i, j, name, _label in ALERTS:
        if i in cols and j in cols:
            alerts[name] = round(float(latest.loc[i, j]), 4)
    return latest, alerts


def main() -> None:
    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    prices = pd.read_csv(path, index_col="date", parse_dates=True)
    prices = prices[[c for c in prices.columns if not c.endswith("_volume")]]
    # 周末行部分标的无值（BTC/外汇 7×24 有、股票/债券无）→ 相关性按交易日算。
    # 行过滤只看核心标的（扩展列 ETH/NG/EURUSD 历史段可能为 NaN，拖累全部行）；
    # 扩展列与核心列的相关性由 rolling corr 按两两有效对计算（dropna 不适用于扩展列）。
    core_cols = [c for c in CORE_TICKERS if c in prices.columns]
    prices = prices.dropna(subset=core_cols)
    if len(prices) < WINDOW + 1:
        logger.warning(f"资产快照仅 {len(prices)} 行，不足 {WINDOW + 1} 行，跳过")
        return

    matrix, alerts = compute_correlation_matrix(prices)
    as_of = str(prices.index[-1].date())

    # 矩阵文件带观测日：首行 index="date"（as_of；窗口大小常量在 cross_asset.WINDOW，
    # server 端 import 使用，不写进 CSV —— 加窗口选择器时再扩展文件格式）
    matrix = pd.concat(
        [
            pd.DataFrame(
                [[as_of] * len(matrix.columns)],
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

    alerts_df = pd.DataFrame([{"date": as_of, **alerts}])
    from .fetchers._io import upsert_rows

    upsert_rows(ALERTS_PATH, alerts_df, subset=["date"], sort_by=["date"])
    logger.info(f"报警标量 → {ALERTS_PATH}: {alerts}")


if __name__ == "__main__":
    main()
