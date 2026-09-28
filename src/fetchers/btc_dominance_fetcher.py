"""BTC/ETH Dominance 日快照（CoinGecko 免费 API，无需 key）。

GET https://api.coingecko.com/api/v3/global → data.market_cap_percentage.{btc,eth}
CoinGecko 只给实时快照（无历史），观测日 = 抓取日（UTC），数值保留 2 位小数。
写入 data/btc_dominance/btc_dominance.csv（观测日 upsert）。

网络：requests 直连，读 HTTPS_PROXY 环境变量（本地 socks5 代理，Actions 直连）。
免费档限速 ~30 RPM，日频 cron 一次足够。

用法:
    uv run python -m src.fetchers.btc_dominance_fetcher
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

API_URL = "https://api.coingecko.com/api/v3/global"
COLUMNS = ["btc_dominance", "eth_dominance"]
OUT = ROOT / "data" / "btc_dominance" / "btc_dominance.csv"


def fetch_dominance() -> pd.DataFrame:
    """拉取 CoinGecko 全球市值占比，返回单行 DataFrame（index=今日 UTC）。"""
    proxies = (
        {"https": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    )
    resp = requests.get(
        API_URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        proxies=proxies,
    )
    resp.raise_for_status()
    pct = resp.json()["data"]["market_cap_percentage"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return pd.DataFrame(
        [
            {
                "date": today,
                "btc_dominance": round(float(pct["btc"]), 2),
                "eth_dominance": round(float(pct["eth"]), 2),
            }
        ]
    ).set_index("date")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="全量覆盖旧文件")
    args = parser.parse_args()

    df = fetch_dominance()
    upsert_timeseries(OUT, df, backfill=args.backfill, column_order=COLUMNS)
    logger.info(
        "btc_dominance upsert → %s（%s：BTC %.2f / ETH %.2f）",
        OUT,
        df.index[0],
        df["btc_dominance"].iloc[0],
        df["eth_dominance"].iloc[0],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
