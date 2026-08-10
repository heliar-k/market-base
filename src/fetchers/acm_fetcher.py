"""Fetch NY Fed ACM 10Y Term Premium (ACMTP10).

数据源: NY Fed 期限溢价交互图底稿 CSV（官方、免费、无 key）
  https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv

ACM 模型（Adrian-Crump-Moench, 2013）10Y 期限溢价，月度月末重估，
1961-06 起全量历史；交互图每周更新底稿。FRED 无此系列
（THREEFYTP10 是 Kim-Wright 模型，不同口径）。

输出: 合并进 data/fred/rates/rates.csv 的 ACMTP10 列（观测日为 key，全量
upsert）。与 fetch_fred 互不覆盖：upsert_timeseries 按列并集合并（BGCR 同款）。

用法:
  uv run python -m src.fetchers.acm_fetcher
"""

import logging
from io import StringIO

import pandas as pd
import requests

from ..config import ROOT, config

logger = logging.getLogger(__name__)

CSV_URL = (
    "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/"
    "acmPlot_data.csv"
)


def fetch_acm() -> pd.DataFrame:
    """拉取 ACM 10Y 期限溢价全量历史。

    返回 DataFrame: index=月末(ISO str)，列=[ACMTP10]（%）；无数据时为空。
    源列: RunDates(DD-Mon-YYYY), TERMYld=10Y 期限溢价, ACMFITYld=拟合 10Y,
    GSWYld=GSW 拟合收益率——只保留期限溢价。
    """
    resp = requests.get(
        CSV_URL,
        timeout=30,
        proxies={"http": None, "https": None},  # 直连（同 treasury_fetcher）
    )
    resp.raise_for_status()
    try:
        df = pd.read_csv(StringIO(resp.text))
    except pd.errors.EmptyDataError:
        return pd.DataFrame()  # 空响应视为无数据
    if df.empty or "RunDates" not in df.columns or "TERMYld" not in df.columns:
        logger.warning("ACM: 源 CSV 结构异常（列=%s），无数据", list(df.columns))
        return pd.DataFrame()

    out = pd.DataFrame(
        {"ACMTP10": pd.to_numeric(df["TERMYld"], errors="coerce").to_numpy()},
        index=pd.to_datetime(
            df["RunDates"], format="%d-%b-%Y", errors="coerce"
        ).dt.strftime("%Y-%m-%d"),
    )
    return out[out.index.notna()].dropna().sort_index()


if __name__ == "__main__":
    import argparse

    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="ACM 10Y 期限溢价（NY Fed）")
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
        f"ACMTP10 upsert: → {path}（{len(df)} 个月, {df.index[0]} → {df.index[-1]}"
        f", 最新 = {df['ACMTP10'].iloc[-1]:.3f}%）"
    )
