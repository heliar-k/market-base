"""欧元区 10Y 主权利率日频（德国 / 法国 / 意大利 / 西班牙），源 CNBC 公开报价。

写入 data/euro_sovereign/yields.csv（观测日 upsert，单位 %，列 DE/FR/IT/ES）。

为什么用 CNBC：欧洲官方体系里没有分国的日频 10Y（都是欧元区聚合或已下线）——
- Eurostat irt_euryld_d / _m、ECB YC：只有 geo=EA 聚合，DE/FR/IT/ES 全 404
- Bundesbank statistic-rmi（BBSIS.D...R10XX）：无需 key，但端点全站冻结在 2026-08-03
  （同端点拿 EUR/USD 也停在 2026-07-13，是快照不是停更）
- Banca d'Italia Infostat：已改成 OAuth 登录墙，新门户无公开直链
- DBnomics BUBA / BDF：Bundesbank 侧停在 2020，BDF 无国债收益率数据集
CNBC ts-api 是实测唯一覆盖四国、免费、无 key、日频（回溯 3 年）的源。

坑（都实测过）：
- 必须带 User-Agent，裸 requests 返回 403 Access Denied
- range 决定分辨率：1M/3M/6M 是日频（6M 给满 3 年），5Y/10Y 退化成周频 → 固定 6M
- 周末行是周五收盘值的复制（942 根里 161 根），入库前按 weekday 过滤，
  否则周末三天同值参与任何均值/相关性计算都是三倍计权
- 非公开文档接口，改版就得换源（候选：investing.com 反爬通道，或各国央行登录源）
"""

import argparse
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

URL = "https://ts-api.cnbc.com/harmony/app/charts/6M.json"
SYMBOLS = {"DE": "DE10Y-DE", "FR": "FR10Y-FR", "IT": "IT10Y-IT", "ES": "ES10Y-ES"}
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _fetch_one(symbol: str, session: requests.Session) -> pd.Series:
    """单国 10Y 收益率日序列（index=日期，value=%，已按日期升序）。"""
    resp = session.get(
        URL,
        params={"symbol": symbol, "timeZone": 0, "responseType": "compact"},
        timeout=30,
    )
    resp.raise_for_status()
    bars = (resp.json().get("barData") or {}).get("priceBars") or []
    pts = {pd.Timestamp(b["tradeTime"][:8]): float(b["close"]) for b in bars if b.get("close")}
    return pd.Series(pts, dtype=float).sort_index()


def fetch_euro_yields() -> pd.DataFrame:
    """四国 10Y 收益率宽表（index=日期，列 DE/FR/IT/ES，单位 %）。"""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    df = pd.DataFrame({k: _fetch_one(v, session) for k, v in SYMBOLS.items()}).sort_index()
    return df[df.index.dayofweek < 5]  # 周末行是周五复制，不是新观测


if __name__ == "__main__":
    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="欧元区 10Y 主权利率（CNBC 日频）")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖（清旧格式）")
    args = parser.parse_args()

    path = ROOT / "data" / "euro_sovereign" / "yields.csv"
    df = fetch_euro_yields()
    upsert_timeseries(path, df, backfill=args.backfill, column_order=list(SYMBOLS))
    if not df.empty:
        span = f"{df.index[0]:%Y-%m-%d}–{df.index[-1]:%Y-%m-%d}"
        print(f"Euro sovereign → {path}（{len(df)} 个交易日，{span}）")
        latest = " · ".join(f"{k} {v:.3f}%" for k, v in df.iloc[-1].to_dict().items())
        print(f"  最新 {df.index[-1]:%Y-%m-%d}: {latest}")
