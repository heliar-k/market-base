"""Fetch commodity futures term structure from Barchart（免费匿名，替代/补充 IBKR）。

IBKR commodities 依赖本地 TWS（Actions 拉不了），Barchart 提供全合约曲线：

- 全品种曲线 → data/barchart/futures/{ROOT}.csv（观测日 upsert 宽表，列=合约代码）
- ZQ 额外写 data/barchart/commodities/ZQ/ZQ_{YYYYMM}.csv（date/close 格式，
  供 rate_expectations_fetcher 在 IBKR 本地数据缺失时降级使用）

数据源: core-api quotes/get?symbol={ROOT}^F&orderBy=contractExpirationDate
⚠️ 延迟报价（lastPrice 常带 's' 后缀），仅收盘/盘后参考，勿用于实时交易决策。

用法:
    ./bin/fetch_barchart_futures               # 全部品种
    ./bin/fetch_barchart_futures --symbols ES,ZQ
"""

import argparse
import logging
from datetime import datetime

import pandas as pd

from ..config import COMMODITY_FUTURES, ROOT
from ._io import upsert_timeseries
from .barchart_client import core_get, to_float

logger = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "data" / "barchart"
# Barchart 与 COMMODITY_FUTURES 品种代码差异（Barchart 用老代码）
BARCHART_ROOT = {"RTY": "TF"}  # Russell 2000 E-mini
FIELDS = (
    "symbol,contractName,contractExpirationDate,lastPrice,"
    "priceChange,highPrice,lowPrice,volume,tradeTime"
)


def fetch_futures_curves(symbols: list[str] | None = None) -> pd.DataFrame:
    """拉取品种全合约曲线，返回单行宽表（date=拉取日，列=合约代码如 ESU31）。

    单个品种失败仅告警跳过，不影响其他品种。
    """
    symbols = symbols or list(COMMODITY_FUTURES)
    row: dict[str, float] = {}
    for root in symbols:
        root2 = BARCHART_ROOT.get(root, root)
        try:
            data = core_get(
                {
                    "symbol": f"{root2}^F",
                    "fields": FIELDS,
                    "orderBy": "contractExpirationDate",
                },
                referer=f"https://www.barchart.com/futures/quotes/{root2}%2A0",
            )
        except Exception as e:
            logger.warning("Barchart %s 曲线拉取失败: %s", root, e)
            continue
        for rec in data.get("data", []):
            price = to_float(rec.get("lastPrice"))
            if price is not None:
                row[rec.get("symbol")] = price
        logger.info(
            "%s(%s): %d 个合约", root, root2, sum(1 for s in row if s.startswith(root2))
        )
    if not row:
        return pd.DataFrame()
    return pd.DataFrame([row], index=[datetime.now().strftime("%Y-%m-%d")])


def _write_zq_files(df: pd.DataFrame, backfill: bool) -> None:
    """从 ZQ 宽表拆出 {合约到期月}.csv（date/close 格式，兼容 rate_expectations）。"""
    zq_cols = [c for c in df.columns if c.startswith("ZQ")]
    if not zq_cols:
        logger.warning("无 ZQ 合约数据")
        return
    # 合约代码后缀字母转月份（F=1月..Z=12月）+ 年份末两位
    month_letter = {m: i + 1 for i, m in enumerate("FGHJKMNQUVXZ")}
    zq_dir = OUTPUT_DIR / "commodities" / "ZQ"
    zq_dir.mkdir(parents=True, exist_ok=True)
    for col in zq_cols:
        code = col[2:]  # e.g. 'N31'
        letter, yy = code[0], code[1:]
        if letter not in month_letter:
            continue
        yyyymm = f"20{yy}{month_letter[letter]:02d}"
        path = zq_dir / f"ZQ_{yyyymm}.csv"
        close = df.iloc[0][col]
        upsert_timeseries(
            path,
            pd.DataFrame([{"close": close}], index=[df.index[0]]),
            backfill=backfill,
        )
        print(f"  ZQ_{yyyymm}: {close}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Barchart 期货期限结构拉取")
    parser.add_argument("--symbols", default="", help="品种列表（逗号分隔，默认全部）")
    parser.add_argument(
        "--backfill", action="store_true", help="全量覆盖（默认 upsert）"
    )
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    df = fetch_futures_curves(symbols)
    if df.empty:
        print("无数据：Barchart 拉取失败")
        raise SystemExit(1)

    futures_dir = OUTPUT_DIR / "futures"
    futures_dir.mkdir(parents=True, exist_ok=True)
    for root in symbols or list(COMMODITY_FUTURES):
        root2 = BARCHART_ROOT.get(root, root)
        cols = [c for c in df.columns if c.startswith(root2)]
        if not cols:
            continue
        path = futures_dir / f"{root}.csv"
        upsert_timeseries(path, df[cols], backfill=args.backfill)
        print(f"→ {path} ({len(cols)} 合约)")

    if not symbols or "ZQ" in symbols:
        _write_zq_files(df, args.backfill)
