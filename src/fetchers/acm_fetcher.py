"""Fetch NY Fed ACM 10Y Term Premium (ACMTP10), daily.

数据源: NY Fed 期限溢价底稿 xls（官方、免费、无 key）
  https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls

ACM 模型（Adrian-Crump-Moench, 2013）10Y 期限溢价：
- "ACM Daily" sheet 含官方日频估算（1961-06-14 起，~16k 行）——
  ACMTP10 列即 10Y 期限溢价（%）；ACMY10 是拟合收益率，勿混用。
- "ACM Monthly" sheet 为月末重估（ACMTermPremium.xls 同文件）。
FRED 无此系列（THREEFYTP10 是 Kim-Wright 模型，不同口径）。

输出: 合并进 data/fred/rates/rates.csv 的 ACMTP10 列（观测日为 key，全量
upsert）。与 fetch_fred 互不覆盖：upsert_timeseries 按列并集合并（BGCR 同款）。

用法:
  uv run python -m src.fetchers.acm_fetcher
"""

import logging

import pandas as pd
import requests
import xlrd

from ..config import ROOT, config

logger = logging.getLogger(__name__)

XLS_URL = (
    "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/"
    "ACMTermPremium.xls"
)
DAILY_SHEET = "ACM Daily"


def _parse_daily(book: "xlrd.Book") -> pd.DataFrame:
    """从 xls book 的 ACM Daily sheet 提取 10Y 期限溢价。

    返回 DataFrame: index=交易日(ISO str)，列=[ACMTP10]（%）。
    只取 DATE + ACMTP10 列（ACMY* 拟合收益率 / ACMRNY* 预期短端均不需要）。
    """
    ws = book.sheet_by_name(DAILY_SHEET)
    hdr = [ws.cell_value(0, c) for c in range(ws.ncols)]
    if "DATE" not in hdr or "ACMTP10" not in hdr:
        raise ValueError(f"ACM xls 缺列（表头: {hdr}）")
    date_col = hdr.index("DATE")
    tp_col = hdr.index("ACMTP10")

    dates, vals = [], []
    for r in range(1, ws.nrows):
        dates.append(ws.cell_value(r, date_col))
        vals.append(ws.cell_value(r, tp_col))
    out = pd.DataFrame(
        {"ACMTP10": pd.to_numeric(vals, errors="coerce")},
        index=pd.to_datetime(dates, format="%d-%b-%Y", errors="coerce").strftime(
            "%Y-%m-%d"
        ),
    )
    out.index.name = "date"
    return out[out.index.notna()].dropna().sort_index()


def fetch_acm() -> pd.DataFrame:
    """拉取 ACM 10Y 期限溢价日频全量历史；网络/解析失败抛异常。"""
    resp = requests.get(
        XLS_URL,
        timeout=60,
        proxies={"http": None, "https": None},  # 直连（同 treasury_fetcher）
    )
    resp.raise_for_status()
    book = xlrd.open_workbook(file_contents=resp.content)
    return _parse_daily(book)


if __name__ == "__main__":
    import argparse

    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="ACM 10Y 期限溢价（NY Fed，日频）")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖重拉")
    args = parser.parse_args()

    path = ROOT / "data" / "fred" / "rates" / "rates.csv"
    df = fetch_acm()
    if df.empty:
        print("ACM: 拉取无数据，中止")
        raise SystemExit(1)

    # ACMTP10 是外部合并列：按 FRED rates 键序排前、ACMTP10 固定排尾（BGCR 同款）
    upsert_timeseries(
        path,
        df,
        backfill=args.backfill,
        column_order=list(config.fred_series["rates"]),
    )
    print(
        f"ACMTP10 upsert: → {path}（{len(df)} 个交易日, {df.index[0]} → {df.index[-1]}"
        f", 最新 = {df['ACMTP10'].iloc[-1]:.3f}%）"
    )
