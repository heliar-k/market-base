"""
Fetch NY Fed Survey of Consumer Expectations (SCE) — 1Y/3Y 通胀预期中位数。
下载全量 Excel，读 'Inflation expectations' sheet，写入 data/sce/sce.csv。

数据源: https://www.newyorkfed.org/microeconomics/sce
每月第二个周一发布。Excel 含 45 个 sheet（通胀/劳动力/房价/信贷等全套），
此处只取追踪所需的 1Y/3Y 通胀预期中位数；其余 sheet 按需扩展。
"""

import io
import logging
import re

import pandas as pd
import requests

from .quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)

SCE_URL = (
    "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/"
    "data/frbny-sce-data.xlsx"
)
_SHEET = "Inflation expectations"
# 表头在第 4 行（0-indexed row 3）；列名 2026-07-29 核实
_HEADER_ROW = 3
# 1Y/3Y 中位数：用子串匹配，容忍大小写/空格差异；
# 'expected inflation' 排除 'Median point prediction ...' 列（那是点预测中位数）
_1Y_KEYS = ["median", "one-year", "expected inflation"]
_3Y_KEYS = ["median", "three-year", "expected inflation"]


def fetch_sce() -> list[DataPoint]:
    """下载 SCE Excel，提取最新月 1Y/3Y 通胀预期中位数。"""
    dps = [
        DataPoint(
            metric="SCE_INFL_1Y_MEDIAN",
            source="NY Fed SCE / Inflation expectations",
            formula="median 1-year ahead expected inflation (%)",
        ),
        DataPoint(
            metric="SCE_INFL_3Y_MEDIAN",
            source="NY Fed SCE / Inflation expectations",
            formula="median 3-year ahead expected inflation (%)",
        ),
    ]
    try:
        resp = requests.get(SCE_URL, timeout=60)
        resp.raise_for_status()
        df = pd.read_excel(
            io.BytesIO(resp.content), sheet_name=_SHEET, header=_HEADER_ROW
        )
        # 日期列无表头（NaN），按位置取第 0 列；去掉日期为空的行
        mask = df.iloc[:, 0].notna()
        df = df[mask]
        latest = df.iloc[-1]

        col_1y = _find_col(df.columns, _1Y_KEYS)
        col_3y = _find_col(df.columns, _3Y_KEYS)
        if not col_1y or not col_3y:
            raise ValueError(f"找不到 1Y/3Y 中位数列；现有列: {list(df.columns)}")

        as_of = _parse_yyyymm(str(latest.iloc[0]))
        dps[0].value = round(float(latest[col_1y]), 4)
        dps[1].value = round(float(latest[col_3y]), 4)
        for dp in dps:
            dp.as_of = as_of
            dp.mark_ok()
        logger.info(f"  SCE: as_of={as_of} 1Y={dps[0].value} 3Y={dps[1].value}")
    except Exception as e:
        for dp in dps:
            dp.mark_error(str(e))
        logger.warning(f"SCE failed: {e}")
    return dps


def _find_col(columns, keys: list[str]) -> str:
    """返回首个 lowercased 列名包含全部 keys 的列名。"""
    for c in columns:
        cl = str(c).lower()
        if all(k in cl for k in keys):
            return c
    return ""


def _parse_yyyymm(raw: str) -> str:
    """'201306' 或 '201306.0' → '2013-06-01'。"""
    s = re.sub(r"\.0+$", "", raw.strip())
    m = re.match(r"^(\d{4})(\d{2})$", s)
    if not m:
        return raw.strip()
    return f"{m.group(1)}-{m.group(2)}-01"


if __name__ == "__main__":
    from pathlib import Path

    from ._io import save_daily_csv

    results = fetch_sce()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"SCE: {ok}/{len(results)} OK")

    root = Path(__file__).resolve().parent.parent.parent
    save_daily_csv(root / "data" / "sce" / "sce.csv", results)
    print("  → data/sce/sce.csv")
