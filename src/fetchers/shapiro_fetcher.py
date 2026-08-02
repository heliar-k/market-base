"""
Fetch FRBSF Shapiro 供给/需求 PCE 通胀分解。
4 个 chart CSV（headline/core × monthly/yoy），拆出 supply/demand/ambiguous 贡献。
写入 data/shapiro/shapiro.csv（观测日为 key，全量 upsert）。

数据源: FRBSF — Supply- and Demand-Driven PCE Inflation（下载链接见 SHAPIRO_URLS）
每月 PCE 发布后几天更新。Excel 全量（分品类明细）暂不拉，4 个 chart CSV 已够追踪用。

每次运行都拉源全量历史并 upsert：忘记运行几个月，下次跑自动补齐缺失月份。
"""

import io
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 4 个 chart CSV：headline/core × monthly(年化月度)/yoy(同比)
SHAPIRO_URLS = {
    "HEADLINE_MOM": (
        "https://www.frbsf.org/wp-content/uploads/"
        "supply-demand-pce-headline-monthly-chart-1.csv"
    ),
    "CORE_MOM": (
        "https://www.frbsf.org/wp-content/uploads/"
        "supply-demand-pce-core-monthly-chart-2.csv"
    ),
    "HEADLINE_YOY": (
        "https://www.frbsf.org/wp-content/uploads/"
        "supply-demand-pce-headline-yoy-chart-3.csv"
    ),
    "CORE_YOY": (
        "https://www.frbsf.org/wp-content/uploads/"
        "supply-demand-pce-core-yoy-chart-4.csv"
    ),
}

# 源 CSV 列名 → 输出列名（4 个文件结构一致）
_COL_MAP = {
    "Supply-driven Inflation": "SUPPLY",
    "Demand-driven Inflation": "DEMAND",
    "Ambiguous": "AMBIG",
}


def fetch_shapiro() -> pd.DataFrame:
    """下载 4 个 Shapiro CSV，合并为全量时间序列（index=观测月）。

    单个 CSV 拉取失败时跳过该组列并告警，不影响其余 CSV 的 upsert。
    """
    dfs = []
    for key, url in SHAPIRO_URLS.items():
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df["date"] = df["time_month"].apply(_parse_month)
            df = df.rename(columns=_COL_MAP)
            keep = ["date"] + list(_COL_MAP.values())
            df = df[keep].set_index("date")
            df.columns = [f"SHAPIRO_{key}_{c}" for c in df.columns]
            dfs.append(df)
            logger.info(
                f"  Shapiro {key}: {len(df)} 条 ({df.index[0]} → {df.index[-1]})"
            )
        except Exception as e:
            logger.warning(f"Shapiro {key} 拉取失败，跳过: {e}")
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, axis=1).sort_index()


def _parse_month(raw: str) -> str:
    """'2026m5' → '2026-05-01'。"""
    m = re.match(r"^\s*(\d{4})m(\d{1,2})\s*$", raw)
    if not m:
        return raw.strip()
    return f"{m.group(1)}-{int(m.group(2)):02d}-01"


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="FRBSF Shapiro 供需通胀分解")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量覆盖（清旧格式 junk，干净重来）；默认为 upsert",
    )
    args = parser.parse_args()

    path = ROOT / "data" / "shapiro" / "shapiro.csv"

    df = fetch_shapiro()
    if df.empty:
        print("Shapiro: 全部源拉取失败，无数据写入")
        raise SystemExit(1)

    upsert_timeseries(path, df, backfill=args.backfill)
    mode = "backfill 覆盖" if args.backfill else "upsert"
    print(f"Shapiro {mode}: → {path} ({len(df.columns)} 指标 × {len(df)} 行)")
