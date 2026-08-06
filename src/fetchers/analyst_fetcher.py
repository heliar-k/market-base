"""
Nasdaq 100 分析师目标价快照（timsun /assets/equities 面板数据）。

成分股来自 Wikipedia “List of NASDAQ-100 companies”（约月频更新；
拉取失败用旧成分缓存兜底）；每票 yfinance Ticker.info 拉目标价字段。
每票 yfinance Ticker.info 拉目标价字段。输出 data/analyst/ndx_targets.csv：
长表（一行一票），按 (date, ticker) upsert，保留历史可追踪目标价上修/下修。

网络约定：
  - Wikipedia 走 requests 默认环境变量代理（本地有 socks5 走代理，Actions 无代理直连）
  - yfinance 走 ensure_yf_proxy()（与项目其他 fetcher 一致）
"""

import logging
import time

import pandas as pd

from ..config import ROOT

logger = logging.getLogger(__name__)

COMPONENTS_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
COMPONENTS_PATH = ROOT / "data" / "analyst" / "ndx_components.csv"
TARGETS_PATH = ROOT / "data" / "analyst" / "ndx_targets.csv"
_REQUEST_SLEEP_SECONDS = 0.2  # 103 票连发易触发 yfinance 限流


def fetch_ndx_components() -> pd.DataFrame:
    """从 Wikipedia 抓当前 Nasdaq-100 成分（ticker/company/industry）。

    网络失败时回退读本地缓存（ndx_components.csv，上次成功的成分）。
    """
    from ._wiki import fetch_wiki_tickers

    df = fetch_wiki_tickers(COMPONENTS_URL, COMPONENTS_PATH)
    return df.rename(columns={"category": "industry"})


def _ticker_targets(ticker: str) -> dict | None:
    """单票 yfinance info → 目标价字段；失败（含限流）返回 None。"""
    from .yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf

    info = yf.Ticker(ticker).info
    if not info:
        return None
    mean = info.get("targetMeanPrice")
    if mean is None:  # 无分析师覆盖 → 跳过（timsun 榜单只列有目标价的票）
        return None
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    return {
        "price": price,
        "target_mean": mean,
        "target_high": info.get("targetHighPrice"),
        "target_low": info.get("targetLowPrice"),
        "analysts": info.get("numberOfAnalystOpinions"),
        "rating": info.get("recommendationKey"),
        "sector": info.get("sector"),
        # 隐含空间（现价 → 平均目标），Web 榜单直接消费
        "upside": round((mean / price - 1) * 100, 2) if price else None,
    }


def fetch_ndx_targets() -> pd.DataFrame:
    """全成分票拉目标价 → 长表（date + ticker + 目标价字段）。"""
    components = fetch_ndx_components()
    rows = []
    for _, c in components.iterrows():
        try:
            t = _ticker_targets(c["ticker"])
            if t is None:
                logger.info(f"  - {c['ticker']}: 无分析师覆盖，跳过")
                continue
            rows.append(
                {
                    "ticker": c["ticker"],
                    "company": c["company"],
                    "industry": c["industry"],
                    **t,
                }
            )
            logger.info(
                f"  ✓ {c['ticker']}: mean={t['target_mean']} ({t['analysts']} analysts)"
            )
        except Exception as e:
            logger.info(f"  ✗ {c['ticker']}: {e}")
        time.sleep(_REQUEST_SLEEP_SECONDS)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from datetime import date

    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)

    df = fetch_ndx_targets()
    if df.empty:
        print("目标价: 全部失败，无数据写入")
        raise SystemExit(1)

    # 按 (date, ticker) upsert：同日新值覆盖旧值
    df.insert(0, "date", str(date.today()))
    cols = [
        "date",
        "ticker",
        "company",
        "industry",
        "sector",
        "price",
        "target_mean",
        "target_high",
        "target_low",
        "upside",
        "analysts",
        "rating",
    ]
    df = df[[c for c in cols if c in df.columns]]
    from ._io import upsert_rows

    upsert_rows(TARGETS_PATH, df, subset=["date", "ticker"], sort_by=["date", "ticker"])
    today = str(date.today())
    n_today = len(df[df["date"] == today])
    print(f"目标价: {len(df)} 行（今日 {n_today} 票）→ {TARGETS_PATH}")
