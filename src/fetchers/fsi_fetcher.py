"""Fetch OFR Financial Stress Index (FSI) from the Office of Financial Research.

写入 data/ofr/fsi.csv（观测日为 key，全量 upsert）。

数据源: https://www.financialresearch.gov/financial-stress-index/data/fsi.csv
（官方、免费、无 key；2000-01 至今日频，发布滞后 2 个工作日）
列: 总指数 OFR_FSI + 5 个成分（Credit / Equity valuation / Safe assets /
Funding / Volatility）+ 3 个区域（US / 其他发达经济体 / 新兴市场）。

每次运行都拉源全量历史并 upsert：忘记运行几天，下次跑自动补齐缺失日期。
"""

import logging
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FSI_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"

# 源 CSV 列名 → 仓库列名（去空格 → 大写下划线）
_COLUMN_RENAME = {
    "Date": "date",
    "OFR FSI": "OFR_FSI",
    "Credit": "CREDIT",
    "Equity valuation": "EQUITY_VALUATION",
    "Safe assets": "SAFE_ASSETS",
    "Funding": "FUNDING",
    "Volatility": "VOLATILITY",
    "United States": "US",
    "Other advanced economies": "OTHER_ADVANCED",
    "Emerging markets": "EMERGING_MARKETS",
}


def fetch_ofr_fsi() -> pd.DataFrame:
    """下载 OFR FSI 全量历史 → DataFrame（index=ISO 日期，列见 _COLUMN_RENAME）。"""
    resp = requests.get(FSI_URL, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    if df.empty:
        raise ValueError("empty CSV")
    df = df.rename(columns=_COLUMN_RENAME)
    df = df.set_index("date")
    df.index = df.index.astype(str)
    return df.sort_index()


if __name__ == "__main__":
    from ..config import ROOT
    from ._io import upsert_timeseries

    path = ROOT / "data" / "ofr" / "fsi.csv"

    df = fetch_ofr_fsi()
    if df.empty:
        print("OFR FSI: 拉取失败，无数据写入")
        raise SystemExit(1)

    upsert_timeseries(path, df)
    print(
        f"OFR FSI upsert: → {path} ({len(df.columns)} 指标 × {len(df)} 行, "
        f"{df.index[0]} → {df.index[-1]})"
    )
