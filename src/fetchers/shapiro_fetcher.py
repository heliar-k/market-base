"""
Fetch FRBSF Shapiro 供给/需求 PCE 通胀分解。
4 个 chart CSV（headline/core × monthly/yoy），拆出 supply/demand/ambiguous 贡献。
写入 data/shapiro/shapiro.csv。

数据源: FRBSF — Supply- and Demand-Driven PCE Inflation（下载链接见 SHAPIRO_URLS）
每月 PCE 发布后几天更新。Excel 全量（分品类明细）暂不拉，4 个 chart CSV 已够追踪用。
"""

import io
import logging
import re

import pandas as pd
import requests

from .quality import DataPoint, QAStatus

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

# CSV 列名（4 个文件结构一致，2026-07-29 核实）
_COL_SUPPLY = "Supply-driven Inflation"
_COL_DEMAND = "Demand-driven Inflation"
_COL_AMBIG = "Ambiguous"


def fetch_shapiro() -> list[DataPoint]:
    """下载 4 个 Shapiro CSV，提取最新月的 supply/demand/ambiguous 贡献。"""
    results = []
    for key, url in SHAPIRO_URLS.items():
        results.extend(_fetch_one(key, url))
    return results


def _fetch_one(key: str, url: str) -> list[DataPoint]:
    """单个 CSV → 3 个 DataPoint（supply/demand/ambiguous）。"""
    base = f"SHAPIRO_{key}"  # e.g. SHAPIRO_HEADLINE_YOY
    dps = [
        DataPoint(
            metric=f"{base}_SUPPLY",
            source="FRBSF / Shapiro decomposition",
            formula="supply-driven contribution to PCE inflation (pp)",
        ),
        DataPoint(
            metric=f"{base}_DEMAND",
            source="FRBSF / Shapiro decomposition",
            formula="demand-driven contribution to PCE inflation (pp)",
        ),
        DataPoint(
            metric=f"{base}_AMBIG",
            source="FRBSF / Shapiro decomposition",
            formula="ambiguous contribution to PCE inflation (pp)",
        ),
    ]
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        if df.empty:
            raise ValueError("empty CSV")
        latest = df.iloc[-1]
        as_of = _parse_month(str(latest["time_month"]))
        dps[0].value = round(float(latest[_COL_SUPPLY]), 4)
        dps[1].value = round(float(latest[_COL_DEMAND]), 4)
        dps[2].value = round(float(latest[_COL_AMBIG]), 4)
        for dp in dps:
            dp.as_of = as_of
            dp.mark_ok()
        logger.info(
            f"  Shapiro {key}: as_of={as_of} "
            f"supply={dps[0].value} demand={dps[1].value} ambigu={dps[2].value}"
        )
    except Exception as e:
        for dp in dps:
            dp.mark_error(str(e))
        logger.warning(f"Shapiro {key} failed: {e}")
    return dps


def _parse_month(raw: str) -> str:
    """'2026m5' → '2026-05-01'。"""
    m = re.match(r"^\s*(\d{4})m(\d{1,2})\s*$", raw)
    if not m:
        return raw.strip()
    return f"{m.group(1)}-{int(m.group(2)):02d}-01"


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    results = fetch_shapiro()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"Shapiro: {ok}/{len(results)} OK")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "shapiro" / "shapiro.csv", results)
    print("  → data/shapiro/shapiro.csv")
