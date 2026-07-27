"""
Fetch economic indicators from FRED API.
数据按分类写入 data/fred/{category}/{category}.csv。

用法:
    ./bin/fetch_fred
"""

import logging

from fredapi import Fred

from ..config import FRED_SERIES_FLAT, config
from .quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)


def _get_fred() -> Fred:
    if not config.fred_api_key:
        raise ValueError("FRED_API_KEY not configured")
    return Fred(api_key=config.fred_api_key)


def _fetch_one_fred(fred: Fred, name: str, series_id: str) -> DataPoint:
    """Core fetch logic shared by fetch_all_fred and fetch_single_fred."""
    dp = DataPoint(
        metric=name,
        source=f"FRED / {series_id}",
        formula="time_series.value; most recent non-null observation",
    )
    try:
        series = fred.get_series(series_id)
        valid = series.dropna()
        if valid.empty:
            dp.mark_error("No valid data in series")
            return dp
        dp.value = round(float(valid.iloc[-1]), 6)
        dp.as_of = valid.index[-1].strftime("%Y-%m-%d")
        dp.mark_ok()
    except Exception as e:
        dp.mark_error(str(e))
    return dp


def fetch_all_fred() -> list[DataPoint]:
    """Fetch all configured FRED series. Returns flat list of DataPoints."""
    fred = _get_fred()
    results = []
    for name, series_id in FRED_SERIES_FLAT.items():
        logger.info(f"Fetching {name} ({series_id})...")
        dp = _fetch_one_fred(fred, name, series_id)
        status = "✓" if dp.qa_status == QAStatus.OK else "✗"
        logger.info(f"  {status} {name}: {dp.value} (as_of={dp.as_of})")
        results.append(dp)
    return results


def fetch_single_fred(name: str, series_id: str) -> DataPoint:
    """Fetch a single FRED series by name + ID."""
    fred = _get_fred()
    return _fetch_one_fred(fred, name, series_id)


def fetch_all_fred_backfill() -> dict[str, "DataFrame"]:  # noqa: F821
    """回填模式：每个分类返回完整历史 DataFrame，列=指标名，index=日期。"""
    import pandas as pd

    fred = _get_fred()
    result = {}
    for cat, series_map in config.fred_series.items():
        dfs = []
        for metric, series_id in series_map.items():
            try:
                s = fred.get_series(series_id)
                s = s.dropna()
                if s.empty:
                    logger.warning(f"  {metric}({series_id}): 无数据，跳过")
                    continue
                df = pd.DataFrame({metric: s})
                dfs.append(df)
                logger.info(
                    f"  {metric}({series_id}): {len(s)} 条"
                    f" ({s.index[0].strftime('%Y-%m-%d')} →"
                    f" {s.index[-1].strftime('%Y-%m-%d')})"
                )
            except Exception as e:
                logger.warning(f"  {metric}({series_id}): 拉取失败 → {e}")
        if dfs:
            combined = dfs[0] if len(dfs) == 1 else pd.concat(dfs, axis=1)
            result[cat] = combined.sort_index()
    return result


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    from ._io import save_daily_csv

    parser = argparse.ArgumentParser(description="FRED 数据拉取")
    parser.add_argument(
        "--backfill", action="store_true", help="回填完整历史时间序列（覆盖现有 CSV）"
    )
    args = parser.parse_args()

    config.validate()
    root = Path(__file__).resolve().parent.parent.parent

    if args.backfill:
        print("FRED 回填模式：拉取全部历史时间序列...")
        backfill_data = fetch_all_fred_backfill()
        for cat, df in backfill_data.items():
            path = root / "data" / "fred" / cat / f"{cat}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index_label="date")
            print(f"  → {path} ({len(df.columns)} 指标 × {len(df)} 行)")
        print(f"完成 {len(backfill_data)} 个分类的回填")
    else:
        results = fetch_all_fred()
        ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
        print(f"FRED: {ok}/{len(results)} OK")

        # 按分类写入独立 CSV（每日追加一行）
        for category, series in config.fred_series.items():
            cat_names = set(series.keys())
            cat_results = [r for r in results if r.metric in cat_names]
            if cat_results:
                path = root / "data" / "fred" / category / f"{category}.csv"
                save_daily_csv(path, cat_results)
                print(
                    f"  → data/fred/{category}/{category}.csv ({len(cat_results)} 系列)"
                )
