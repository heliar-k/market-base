"""
Fetch NY Fed Survey of Consumer Expectations (SCE) — 1Y/3Y 通胀预期中位数。
下载全量 Excel，读 'Inflation expectations' sheet，
写入 data/sce/sce.csv（观测日为 key，全量 upsert）。

数据源: https://www.newyorkfed.org/microeconomics/sce
每月第二个周一发布。Excel 含 45 个 sheet（通胀/劳动力/房价/信贷等全套），
此处只取追踪所需的 1Y/3Y 通胀预期中位数；其余 sheet 按需扩展。

每次运行都拉源全量历史并 upsert：忘记运行几个月，下次跑自动补齐缺失月份。
"""

import io
import logging
import re

import pandas as pd
import requests

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


def fetch_sce() -> pd.DataFrame:
    """下载 SCE Excel，返回全量时间序列（index=观测月，列=1Y/3Y 中位数）。"""
    resp = requests.get(SCE_URL, timeout=60)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content), sheet_name=_SHEET, header=_HEADER_ROW)
    mask = df.iloc[:, 0].notna()
    df = df[mask].copy()

    col_1y = _find_col(df.columns, _1Y_KEYS)
    col_3y = _find_col(df.columns, _3Y_KEYS)
    if not col_1y or not col_3y:
        raise ValueError(f"找不到 1Y/3Y 中位数列；现有列: {list(df.columns)}")

    df["date"] = df.iloc[:, 0].astype(str).apply(_parse_yyyymm)
    df["SCE_INFL_1Y_MEDIAN"] = df[col_1y].astype(float)
    df["SCE_INFL_3Y_MEDIAN"] = df[col_3y].astype(float)
    result = df[["date", "SCE_INFL_1Y_MEDIAN", "SCE_INFL_3Y_MEDIAN"]].set_index("date")
    logger.info(f"  SCE: {len(result)} 条 ({result.index[0]} → {result.index[-1]})")
    return result


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
    import argparse
    from pathlib import Path

    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="NY Fed SCE 通胀预期")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量覆盖（清旧格式 junk，干净重来）；默认为 upsert",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    path = root / "data" / "sce" / "sce.csv"

    df = fetch_sce()

    if args.backfill:
        df.to_csv(path, index_label="date")
        print(f"SCE --backfill 覆盖: → {path} ({len(df.columns)} 指标 × {len(df)} 行)")
    else:
        upsert_timeseries(path, df)
        print(f"SCE upsert: → {path} ({len(df.columns)} 指标 × {len(df)} 行)")
