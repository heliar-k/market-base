"""
外汇对日线管线（timsun /assets/fx 面板数据源）。

拉取 config.FX_PAIRS 全部 16 对的 yfinance 日线收盘价，宽表 upsert 到
data/fx/fx_pairs.csv（首次运行自动回填，后续每日增量只追加当日行；
gaps 由每对自身 yf 日线补齐，与 upsert_timeseries 合并语义天然兼容）。

纯 yfinance，无 IBKR 依赖；Actions 每日跑（YF_NO_PROXY=1 直连）。
"""

from __future__ import annotations

import logging

import pandas as pd

from src.config import FX_PAIRS, ROOT

logger = logging.getLogger(__name__)

PATH = ROOT / "data" / "fx" / "fx_pairs.csv"
PERIOD = "2y"  # 回填深度（60D USD 压力需 ≥ 60 交易日，2y 富余）


def fetch_fx_prices(period: str = PERIOD) -> pd.DataFrame:
    """全对日线收盘宽表（index=date tz-free，列=FX_PAIRS key）。"""
    from src.fetchers.yfinance_fetcher import wide_close

    tickers = {key: ticker for key, (ticker, _n, _g) in FX_PAIRS.items()}
    return wide_close(tickers, period)


def main() -> None:
    from src.fetchers._io import upsert_timeseries

    wide = fetch_fx_prices()
    if wide.empty:
        raise SystemExit("fx: 无数据")
    upsert_timeseries(PATH, wide, column_order=list(FX_PAIRS))
    logger.info(f"fx: {len(wide)} 行 → {PATH}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
