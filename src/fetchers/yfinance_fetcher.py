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
from pathlib import Path

import pandas as pd

from ..config import ROOT, config
from .quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)

# ── 代理 URL（纯计算，无副作用）─────────────────────────────────────────
# YF_NO_PROXY=1（GitHub Actions 无代理环境）：跳过代理设置，直连 Yahoo。
# 兜底：.env 没配代理时用项目默认值。
# env 写入与 TCP 探测收敛在 ensure_yf_proxy()；yfinance 在函数内惰性 import。
if os.environ.get("YF_NO_PROXY"):
    _PROXY_URL = ""
else:
    _PROXY_URL = config.https_proxy or "socks5://127.0.0.1:7890"


def ensure_yf_proxy(timeout: float = 3.0) -> None:
    """设置 yfinance 代理环境变量 + TCP 可达性检测。

    在 import yfinance 之前调用。代理不可达时抛出 ConnectionError。
    import 模块本身零副作用（不写 env、不 import yfinance）；
    调用方约定：ensure_yf_proxy() → import yfinance（惰性）。
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
    ensure_yf_proxy()
    import yfinance as yf

    t = yf.Ticker(ticker)
    df = t.history(period=period)
    if df.empty:
        return pd.DataFrame()
    # 统一列名与 ibkr_fetcher 一致；索引清洗（时区剥离 + 归一到零点）
    # 在此一处完成，各调用方不再各自处理（审计 E-9）
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
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
    index 为带时区 datetime）。调用方需先 ensure_yf_proxy()。
    """
    period = {"5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d"}[interval]
    ensure_yf_proxy()
    import yfinance as yf

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
        ensure_yf_proxy()
        import yfinance as yf

        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            dp.mark_error()
            return dp
        # 最后一行可能为 NaN（亚洲指数数据延迟/时区错位）→ 取最近有效收盘
        close_series = hist["Close"].dropna()
        if close_series.empty:
            dp.mark_error()
            return dp
        close = close_series.iloc[-1]
        ts = hist.loc[close_series.index[-1]].name
        dp.value = round(float(close), 4)
        # 同日成交量（部分品种/时段可能缺失 → None，容忍）
        vol_series = (
            hist["Volume"].dropna() if "Volume" in hist else pd.Series(dtype=float)
        )
        dp.volume = round(float(vol_series.iloc[-1])) if not vol_series.empty else None
        dp.as_of = ts.strftime("%Y-%m-%d")
        dp.mark_ok()
    except Exception as e:
        logger.info(f"  ✗ {name}: {e}")
        dp.mark_error()
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


# 快照最少行数：credit_analysis 的 Market Liquidity 子分需要 22 日动量（≥23 条）
_SEED_MIN_ROWS = 23


def seed_history_if_short(filepath: Path, min_rows: int = _SEED_MIN_ROWS) -> None:
    """快照行数不足 min_rows 时，用 yfinance 日线历史回填（close + volume）。

    日频快照每天只追加 1 行，新建文件后要 ~1 个月才能支撑 HYG/LQD 22 日动量
    与 KBWB 偏离度（审计 P1-⑩）。回填只在新文件/长期未跑时触发一次；
    拉取复用 fetch_ohlcv（索引清洗/列名统一），写盘复用 _io.upsert_timeseries
    按观测日合并（审计 D-6），不覆盖既有行。失败逐品种容忍，不影响当日快照。
    """
    from ._io import load_timeseries, upsert_timeseries

    existing = load_timeseries(filepath)
    if len(existing) >= min_rows:
        return
    ensure_yf_proxy()

    # 每个品种一段（date × [close, volume]），外连接拼宽表 → 全品种同日期同行
    frames: list[pd.DataFrame] = []
    for name, ticker in config.yf_tickers.items():
        try:
            df = fetch_ohlcv(ticker, period="3mo")
        except Exception as e:
            logger.info(f"  ✗ 回填 {name}: {e}")
            continue
        if df.empty:
            continue
        vol = df["volume"] if "volume" in df else pd.Series(dtype=float)
        frames.append(pd.DataFrame({name: df["close"], f"{name}_volume": vol}))
    if not frames:
        return
    wide = pd.concat(frames, axis=1).sort_index()
    # 列序与 save_daily_csv 首次创建一致：价格列在前、volume 列在后
    wide = wide[
        [c for c in wide.columns if not c.endswith("_volume")]
        + [c for c in wide.columns if c.endswith("_volume")]
    ]
    upsert_timeseries(filepath, wide)
    logger.info(f"  yfinance: 历史回填 {len(wide)} 行 → {filepath.name}")


if __name__ == "__main__":
    from ._io import save_daily_csv

    results = fetch_all_assets()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"yfinance: {ok}/{len(results)} OK")

    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    save_daily_csv(path, results)
    print("  → data/yfinance/asset_prices.csv")
    seed_history_if_short(path)
