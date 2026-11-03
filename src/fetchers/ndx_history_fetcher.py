from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from ..config import ROOT
from ._io import upsert_timeseries
from .yfinance_fetcher import fetch_ohlcv

OUTPUT = ROOT / "data" / "indices" / "NDX.csv"


def fetch_ndx_history() -> pd.DataFrame:
    """拉取 NDX 全历史未复权日线；源只返回近期片段时直接报错。"""
    frame = fetch_ohlcv("^NDX", period="max", auto_adjust=False)
    if frame.empty:
        raise RuntimeError("Yahoo Finance 未返回 NDX 历史")
    if frame.index[0] > pd.Timestamp("1990-01-01") or len(frame) < 5000:
        raise RuntimeError(
            f"NDX 历史不完整: {len(frame)} 行，起点 {frame.index[0].date()}，拒绝按全历史入库"
        )
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="NDX 全历史日线（Yahoo Finance）")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    frame = fetch_ndx_history()
    upsert_timeseries(args.output, frame, column_order=list(frame.columns))
    print(
        f"NDX 全历史 → {args.output}（{len(frame)} 行，"
        f"{frame.index[0].date()} → {frame.index[-1].date()}）"
    )


if __name__ == "__main__":
    main()
