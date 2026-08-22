"""
ETF 数据管线（timsun /assets/etfs 面板数据源）。

两个文件：
  - data/etf/universe.csv   全量美国上市 ETF 清单（Nasdaq Trader Symbol Directory，
                            nasdaqlisted + otherlisted，ETF=Y 过滤），名称关键词
                            规则分类（config.ETF_KEYWORDS，timsun 口径简化版）
  - data/etf/pool_prices.csv  精选池 25 只日线收盘宽表（yfinance，回填 1y）

纯公开数据，无 IBKR 依赖；Actions 每日跑。
"""

from __future__ import annotations

import logging

import pandas as pd

from src.config import ETF_KEYWORDS, ETF_POOL, ROOT

logger = logging.getLogger(__name__)

UNIVERSE_PATH = ROOT / "data" / "etf" / "universe.csv"
POOL_PATH = ROOT / "data" / "etf" / "pool_prices.csv"

_NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _classify(name: str) -> str:
    """名称关键词 → 分类（首个命中；broad 兜底）。"""
    n = name.lower()
    for cat, kws in ETF_KEYWORDS.items():
        if cat == "broad":
            continue
        if any(k in n for k in kws):
            return cat
    return "broad"


def fetch_universe() -> pd.DataFrame:
    """Nasdaq Trader 全量 ETF 清单（symbol, name, category）。"""
    import requests

    rows: list[dict[str, str]] = []
    for url in [_NASDAQ_LISTED, _OTHER_LISTED]:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = resp.text.strip()
        # 首行表头 + 末尾 "$END$" 行；两个文件列名不同（Symbol / ACT Symbol）
        lines = [s for s in text.splitlines() if s and s != "$END$"]
        header = lines[0].split("|")
        sym_col = "Symbol" if "Symbol" in header else "ACT Symbol"
        for line in lines[1:]:
            rec = dict(zip(header, line.split("|")))
            if rec.get("ETF", "").strip().upper() != "Y":
                continue
            sym = rec.get(sym_col, "").strip()
            name = rec.get("Security Name", "").strip()
            if not sym:
                continue
            rows.append({"symbol": sym, "name": name, "category": _classify(name)})
    return pd.DataFrame(rows).drop_duplicates(subset="symbol")


def fetch_pool_prices(period: str = "1y") -> pd.DataFrame:
    """精选池日线收盘宽表（index=date，列=ETF_POOL key）。"""
    from src.fetchers.yfinance_fetcher import fetch_ohlcv

    frames: list[pd.DataFrame] = []
    for ticker in ETF_POOL:
        try:
            df = fetch_ohlcv(ticker, period=period)
        except Exception as e:
            logger.warning(f"✗ {ticker}: {e}")
            continue
        if df.empty:
            continue
        frames.append(pd.DataFrame({ticker: df["close"]}))
    if not frames:
        return pd.DataFrame()
    wide = pd.concat(frames, axis=1).sort_index()
    return wide[~wide.index.duplicated(keep="last")]


def main() -> None:
    from src.fetchers._io import upsert_timeseries

    uni = fetch_universe()
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    uni.to_csv(UNIVERSE_PATH, index=False)
    logger.info(f"etf universe: {len(uni)} 行 → {UNIVERSE_PATH}")

    pool = fetch_pool_prices()
    upsert_timeseries(POOL_PATH, pool, column_order=list(ETF_POOL))
    logger.info(f"etf pool: {len(pool)} 行 → {POOL_PATH}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
