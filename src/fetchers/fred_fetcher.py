"""
Fetch economic indicators from FRED API.
数据按分类写入 data/fred/{category}/{category}.csv（观测日为 key，全量 upsert）。

每次运行都拉每个系列的全量历史并 upsert：忘记运行几天/几周，下次跑自动补齐缺失日期。
fredapi 的 get_series 本就返回全量序列，upsert 只是不再丢弃历史，零额外拉取成本。

用法:
    ./bin/fetch_fred              # 全量 upsert（默认）
    ./bin/fetch_fred --backfill   # 全量覆盖（清旧格式 junk）
"""

import logging

from fredapi import Fred

from ..config import config

logger = logging.getLogger(__name__)


def _get_fred() -> Fred:
    if not config.fred_api_key:
        raise ValueError("FRED_API_KEY not configured")
    return Fred(api_key=config.fred_api_key)


def fetch_all_fred() -> dict[str, "object"]:
    """拉取全部 FRED 系列，按分类返回 {category: DataFrame}。

    每个 DataFrame index=观测日（字符串），列=指标名。单系列失败跳过并告警，不影响其余。
    """
    import pandas as pd

    fred = _get_fred()
    result: dict[str, pd.DataFrame] = {}
    for cat, series_map in config.fred_series.items():
        dfs = []
        for metric, series_id in series_map.items():
            try:
                s = fred.get_series(series_id).dropna()
                if s.empty:
                    logger.warning(f"  {metric}({series_id}): 无数据，跳过")
                    continue
                df = pd.DataFrame({metric: s})
                df.index = df.index.strftime("%Y-%m-%d")
                dfs.append(df)
                logger.info(
                    f"  {metric}({series_id}): {len(s)} 条"
                    f" ({df.index[0]} → {df.index[-1]})"
                )
            except Exception as e:
                logger.warning(f"  {metric}({series_id}): 拉取失败 → {e}")
        if dfs:
            combined = dfs[0] if len(dfs) == 1 else pd.concat(dfs, axis=1)
            result[cat] = combined.sort_index()
    return result


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="FRED 数据拉取")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量覆盖（清旧格式 junk，干净重来）；默认为 upsert",
    )
    args = parser.parse_args()

    config.validate()
    root = ROOT

    print("FRED: 拉取全部系列全量历史...")
    backfill_data = fetch_all_fred()
    for cat, df in backfill_data.items():
        path = root / "data" / "fred" / cat / f"{cat}.csv"
        upsert_timeseries(path, df, backfill=args.backfill)
        print(f"  → data/fred/{cat}/{cat}.csv ({len(df.columns)} 指标 × {len(df)} 行)")
    print(f"完成 {len(backfill_data)} 个分类")
