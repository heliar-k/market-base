#!/usr/bin/env python3
"""股票日线拉取 — IBKR 优先，yfinance 回退。"""

import argparse
import logging

from ..config import STOCKS
from ._symbol_fetch import run


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="股票日线拉取（IBKR 优先，yfinance 回退）"
    )
    parser.add_argument("--symbols", help="逗号分隔的品种名称（不指定则拉全部）")
    parser.add_argument("--days", type=int, help="拉取最近 N 天")
    parser.add_argument("--dry-run", action="store_true", help="仅检查连接")
    parser.add_argument("--client-id", type=int, help="指定 clientId（默认随机）")
    args = parser.parse_args()
    run(STOCKS, "stock", args)


if __name__ == "__main__":
    main()
