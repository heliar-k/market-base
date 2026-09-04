"""Barchart 波动率指数快照（30 指数全景，timsun dashboard 对齐源）。

timsun.net/volatility/dashboard 的「Barchart snapshot」即 core-api
`list=stocks.markets.volatility`：一次返回 30 个波动率指数的
lastPrice + 1D/5D/1M/1Y 变化，免费（直连失败自动降级无头浏览器，见 barchart_client）。

本仓库 CBOE / yfinance 已覆盖其中 28 个（全量历史），此 fetcher 补齐
VXMO（CBOE Standard Monthly VIX）、VXEF（EFA VIX，CBOE 已停发）
两个免费无历史源的指数，逐日快照积累本地历史；同时存全 30 指数作交叉校验。

写入 data/barchart/volatility_snapshot.csv（观测日 upsert 宽表）：
  date, VIX, VIX_chg1d, VIX_chg5d, VIX_chg1m, VIX_chg1y, VIXD, ...
变化列为百分数（-2.60 即 -2.60%，与 analysis_utils.chg_pct 同口径），
来自 Barchart 官方字段，VXMO/VXEF 无本地历史时的兜底。
"""

import logging
from datetime import datetime

import pandas as pd

from .barchart_client import core_get, to_float

logger = logging.getLogger(__name__)

_REFERER = "https://www.barchart.com/stocks/indices/volatility"
_FIELDS = (
    "symbol,lastPrice,percentChange,percentChange5d,percentChange1m,percentChange1y"
)

# Barchart symbol → 本仓库列名（$ 前缀去掉即同）
_CHG_COLS = {
    "percentChange": "chg1d",
    "percentChange5d": "chg5d",
    "percentChange1m": "chg1m",
    "percentChange1y": "chg1y",
}


def fetch_volatility_snapshot() -> pd.DataFrame:
    """拉取 30 指数快照 → 单行宽表（index=拉取日，列={SYM} 与 {SYM}_chg*）。"""
    data = core_get(
        {
            "list": "stocks.markets.volatility",
            "fields": _FIELDS,
            "page": 1,
            "limit": 100,
            "raw": 1,
        },
        referer=_REFERER,
    )
    row: dict[str, float] = {}
    for rec in data.get("data", []):
        sym = str(rec.get("symbol", "")).lstrip("$")
        if not sym:
            continue
        last = to_float(rec.get("lastPrice"))
        if last is not None:
            row[sym] = last
        for key, suffix in _CHG_COLS.items():
            v = to_float(rec.get(key))
            if v is not None:  # to_float 把 '28.39%' 转成 0.2839，×100 回百分数
                row[f"{sym}_{suffix}"] = round(v * 100, 2)
    if not row:
        return pd.DataFrame()
    logger.info("Barchart 波动率: %d 个指数", sum(1 for c in row if "_" not in c))
    return pd.DataFrame([row], index=[datetime.now().strftime("%Y-%m-%d")])


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="Barchart 波动率指数快照")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖；默认 upsert")
    args = parser.parse_args()

    path = ROOT / "data" / "barchart" / "volatility_snapshot.csv"
    df = fetch_volatility_snapshot()
    if df.empty:
        print("Barchart 波动率: 拉取失败，无数据写入")
        raise SystemExit(1)

    # 价格列在前、变化列在后（与 yfinance asset_prices 同款列序约定）
    price_cols = sorted(c for c in df.columns if "_" not in c)
    chg_cols = sorted(c for c in df.columns if "_" in c)
    upsert_timeseries(path, df[price_cols + chg_cols], backfill=args.backfill)
    mode = "backfill 覆盖" if args.backfill else "upsert"
    print(f"Barchart 波动率 {mode}: → {path} ({len(df.columns)} 列)")
