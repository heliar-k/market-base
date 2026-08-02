"""
Fetch core asset prices via yfinance.
Needs SOCKS5 proxy: socks5://127.0.0.1:7890

其他模块用到 yfinance 时，先调 ensure_yf_proxy() 做代理设置 + 可达性检测：
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy
    ensure_yf_proxy()
    import yfinance as yf
"""

import logging
import os

import pandas as pd

from ..config import ROOT, config
from .quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)

# ── 代理设置：必须在 import yfinance 前执行 ──────────────────────────────
# YF_NO_PROXY=1（GitHub Actions 无代理环境）：跳过代理设置，直连 Yahoo。
# 兜底：.env 没配代理时用项目默认值
if os.environ.get("YF_NO_PROXY"):
    _PROXY_URL = ""
else:
    _PROXY_URL = config.https_proxy or "socks5://127.0.0.1:7890"
os.environ.setdefault("HTTPS_PROXY", _PROXY_URL)
os.environ.setdefault("HTTP_PROXY", _PROXY_URL)

import yfinance as yf  # noqa: E402


def ensure_yf_proxy(timeout: float = 3.0) -> None:
    """设置 yfinance 代理环境变量 + TCP 可达性检测。

    在 import yfinance 之前调用。代理不可达时抛出 ConnectionError。
    已在模块级别设置过代理，此函数主要做可达性检测（幂等，可多次调用）。
    """
    from urllib.parse import urlparse

    os.environ.setdefault("HTTPS_PROXY", _PROXY_URL)
    os.environ.setdefault("HTTP_PROXY", _PROXY_URL)

    u = urlparse(_PROXY_URL)
    host, port = u.hostname, u.port
    if not host or not port:
        return

    try:
        import socket

        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
    except OSError:
        raise ConnectionError(
            f"代理 {host}:{port} 不可达。\n"
            f"  macOS: 检查 Clash/V2Ray/Surge 是否运行。\n"
            f"  或修改 .env 中的 HTTPS_PROXY 为正确地址。"
        ) from None


def fetch_ohlcv(ticker: str, period: str = "2y") -> pd.DataFrame:
    """拉取 OHLCV 日线，返回 DataFrame，列: open/high/low/close/volume。

    用于 IBKR 不可用时的回退。index 为 date (datetime)。
    """
    logger.info(f"[yfinance fallback] 拉取 {ticker} OHLCV (period={period})...")
    t = yf.Ticker(ticker)
    df = t.history(period=period)
    if df.empty:
        return pd.DataFrame()
    # 统一列名与 ibkr_fetcher 一致
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "date"
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_index()


def yf_minute_bars(ticker: str, interval: str) -> pd.DataFrame:
    """yfinance 分钟线 OHLCV（韩股等 IBKR 无权限品种的回退源）。

    interval: "5m" | "15m" | "1h" | "4h"（深度：5m/15m 60 天，1h/4h 2 年）
    Returns 与 ibkr bars_to_dataframe 同构（open/high/low/close/volume，
    index 为带时区 datetime）。模块级代理已设置，无需额外处理。
    """
    period = {"5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d"}[interval]
    h = yf.Ticker(ticker).history(period=period, interval=interval)
    if h.empty:
        return pd.DataFrame()
    df = h.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


def _fetch_ticker(ticker: str, name: str) -> DataPoint:
    """Fetch a single yfinance ticker and return a DataPoint."""
    dp = DataPoint(
        metric=name,
        source="Yahoo Finance / yfinance",
        formula="asset_prices.close, daily close",
    )
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            dp.mark_error("No data returned from yfinance")
            return dp
        # 最后一行可能为 NaN（亚洲指数数据延迟/时区错位）→ 取最近有效收盘
        close_series = hist["Close"].dropna()
        if close_series.empty:
            dp.mark_error("No valid close in yfinance data")
            return dp
        close = close_series.iloc[-1]
        ts = hist.loc[close_series.index[-1]].name
        dp.value = round(float(close), 4)
        dp.as_of = ts.strftime("%Y-%m-%d")
        dp.mark_ok()
    except Exception as e:
        dp.mark_error(str(e))
    return dp


def fetch_all_assets() -> list[DataPoint]:
    """Fetch all configured yfinance tickers. Returns list of DataPoints."""
    results = []
    for name, ticker in config.yf_tickers.items():
        logger.info(f"Fetching {name} ({ticker})...")
        dp = _fetch_ticker(ticker, name)
        results.append(dp)
        status = "✓" if dp.qa_status == QAStatus.OK else "✗"
        logger.info(f"  {status} {name}: {dp.value} (as_of={dp.as_of})")
    return results


if __name__ == "__main__":
    from ._io import save_daily_csv

    results = fetch_all_assets()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"yfinance: {ok}/{len(results)} OK")

    save_daily_csv(ROOT / "data" / "yfinance" / "asset_prices.csv", results)
    print("  → data/yfinance/asset_prices.csv")
