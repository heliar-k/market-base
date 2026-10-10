"""Farside ETH 现货 ETF 资金流。

与 etf_flows_fetcher（BTC 版）同模式：Farside 官网直连 403 → 经 Jina Reader。
ETH 页 11 ETF + Total（2024-07-23 起，单位 M USD，负值 = 净流出），
解析函数直接复用 BTC 版，仅列清单 / URL / 输出路径不同。

写入 data/etf_flows_eth/etf_flows_eth.csv（观测日 upsert 宽表）。
列序: date + ETHA ETHB FETH ETHW TETH ETHV QETH EZET MSSE ETHE ETH Total。

用法:
    uv run python -m src.fetchers.etf_flows_eth_fetcher           # 拉全量并 upsert
    uv run python -m src.fetchers.etf_flows_eth_fetcher --backfill  # 全量覆盖
"""

from __future__ import annotations

import argparse
import logging

from ..config import ROOT
from ._io import upsert_timeseries
from .etf_flows_fetcher import fetch_flows

logger = logging.getLogger(__name__)

PAGE_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"

# Farside ETH 页表头同序；Total = 11 ETF 当日净流入合计
COLUMNS = [
    "ETHA",
    "ETHB",
    "FETH",
    "ETHW",
    "TETH",
    "ETHV",
    "QETH",
    "EZET",
    "MSSE",
    "ETHE",
    "ETH",
    "Total",
]

OUT = ROOT / "data" / "etf_flows_eth" / "etf_flows_eth.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="全量覆盖旧文件")
    args = parser.parse_args()

    df = fetch_flows(PAGE_URL, COLUMNS)
    upsert_timeseries(OUT, df, backfill=args.backfill, column_order=COLUMNS)
    logger.info(
        "etf_flows_eth upsert → %s 行（%s 起 / %s 止）",
        OUT,
        df.index[0],
        df.index[-1],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
