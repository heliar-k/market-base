"""Fetch Broad General Collateral Rate (BGCR) from NY Fed Markets API.

数据源: https://markets.newyorkfed.org/api/rates/secured/bgcr/search.json
（官方、免费、无 key；FRED 无 BGCR 系列，SOFR 家族中仅 TGCR/SOFR 上 FRED）

BGCR = TGCR + GCF Repo 交易，是 SOFR 的中间口径（TGCR ⊂ BGCR ⊂ SOFR）。

输出: 合并进 data/fred/rates/rates.csv 的 BGCR 列（观测日为 key，全量 upsert）。
与 fetch_fred 互不覆盖：upsert_timeseries 按列并集合并，fetch_fred 无 BGCR 列
时旧值保留。

用法:
  uv run python -m src.fetchers.bgcr_fetcher
"""

import logging
from datetime import date, timedelta

import pandas as pd
import requests

from ..config import ROOT, config

logger = logging.getLogger(__name__)

API_URL = "https://markets.newyorkfed.org/api/rates/secured/bgcr/search.json"
BGCR_START = "2021-03-01"  # NY Fed 2021-03-01 起发布 TGCR/BGCR


def fetch_bgcr(start: str, end: str) -> pd.DataFrame:
    """拉取 [start, end]（含）区间 BGCR 日频利率。

    返回 DataFrame: index=生效日(ISO str)，列=[BGCR]；无数据时为空。
    """
    resp = requests.get(
        API_URL,
        params={"startDate": start, "endDate": end},
        timeout=30,
        proxies={"http": None, "https": None},  # 直连（同 treasury_fetcher）
    )
    resp.raise_for_status()
    rows = resp.json().get("refRates", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        {
            "BGCR": [r["percentRate"] for r in rows],
        },
        index=[r["effectiveDate"] for r in rows],
    )
    df.index.name = "date"
    return df.sort_index()


if __name__ == "__main__":
    import argparse

    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="BGCR 利率（NY Fed Markets API）")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖重拉")
    args = parser.parse_args()

    path = ROOT / "data" / "fred" / "rates" / "rates.csv"

    if args.backfill:
        start, label = BGCR_START, "全量"
    else:
        # 增量 5 天覆盖工作日；首次（无 BGCR 列）自动拉全量
        old = pd.read_csv(path, index_col="date") if path.exists() else pd.DataFrame()
        if "BGCR" not in old.columns or old["BGCR"].isna().all():
            start, label = BGCR_START, "首次全量"
        else:
            start = (date.today() - timedelta(days=5)).isoformat()
            label = "增量 5 天"
    end = date.today().isoformat()

    df = fetch_bgcr(start, end)
    if df.empty:
        print(f"BGCR: {label} 区间内无数据（{start} ~ {end}）")
        raise SystemExit(1)

    # BGCR 是外部合并列：按 FRED rates 键序排前、BGCR 固定排尾（审计 F-10）
    upsert_timeseries(
        path,
        df,
        backfill=args.backfill,
        column_order=list(config.fred_series["rates"]),
    )
    print(
        f"BGCR {label} upsert: → {path}（{len(df)} 个交易日, 最新 {df.index[-1]} "
        f"= {df['BGCR'].iloc[-1]:.2f}%）"
    )
