"""
Fetch quarterly & annual financial statements
(income / balance / cashflow) via yfinance.

数据源: Yahoo Finance（走 .env 的 SOCKS5 代理；Actions 用 YF_NO_PROXY=1 直连）
输出: data/financials/{SYMBOL}/{statement}_{period}.csv
  statement: income | balance | cashflow
  period:    annual（近 4 年）| quarterly（近 4-8 季度）
CSV 结构: 行 = 报告期末（period end，观测日 upsert），列 = 科目
  （upsert_timeseries 模式：同日新值覆盖，新期追加）
ETF（SPY/QQQ）无财报，自动跳过。

用法:
  uv run python -m src.fetchers.financials_fetcher
  uv run python -m src.fetchers.financials_fetcher --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from ..config import ROOT, STOCKS
from ._io import upsert_timeseries
from .yfinance_fetcher import ensure_yf_proxy

logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "data" / "financials"

# 年度/季度 × 三张表：yfinance 属性 → 文件名
STATEMENTS = [
    ("income_stmt", "income_annual"),
    ("quarterly_income_stmt", "income_quarterly"),
    ("balance_sheet", "balance_annual"),
    ("quarterly_balance_sheet", "balance_quarterly"),
    ("cashflow", "cashflow_annual"),
    ("quarterly_cashflow", "cashflow_quarterly"),
]


def _fetch_symbol(yf_ticker: str) -> dict[str, pd.DataFrame]:
    """拉取一只股票的三张表（年度+季度）。返回 {文件名: DataFrame(期末日期×科目)}。"""
    ensure_yf_proxy()
    import yfinance as yf  # 惰性 import：代理 env 必须在 import 前设置

    t = yf.Ticker(yf_ticker)
    out: dict[str, pd.DataFrame] = {}
    for attr, name in STATEMENTS:
        df = getattr(t, attr)
        if df is None or df.empty:
            continue  # ETF/新上市无数据
        out[name] = df.T  # 科目 → 行，报告期末 → 索引
    return out


def fetch_financials(symbols: list[str] | None = None) -> dict[str, int]:
    """拉取配置全部股票的财报三张表。symbols=None 时用全部。
    返回 {symbol: 写入文件数}。"""
    result: dict[str, int] = {}
    for sc in STOCKS:
        ticker = sc.yf_ticker or sc.name
        if symbols and sc.name not in symbols and ticker not in symbols:
            continue
        try:
            frames = _fetch_symbol(ticker)
        except Exception as e:  # noqa: BLE001 — 单只失败不阻断整体
            logger.warning("%s 拉取失败: %s", sc.name, e)
            continue
        n = 0
        out_dir = OUT_DIR / sc.name
        for name, df in frames.items():
            if df.empty:
                continue
            upsert_timeseries(out_dir / f"{name}.csv", df)
            n += 1
        result[sc.name] = n
        logger.info("%s: 写入 %d 张表", sc.name, n)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch quarterly/annual financial statements"
    )
    parser.add_argument("--symbols", help="逗号分隔的股票列表（默认全部）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    result = fetch_financials(symbols)
    logger.info("完成: %d 只股票", len(result))


if __name__ == "__main__":
    main()
