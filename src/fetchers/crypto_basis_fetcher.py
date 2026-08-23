"""CME BTC 期货年化基差日序列（timsun V1 治理口径）。

数据源：Yahoo Finance chart API（免 key，日线 close）
  - BTC=F：CME 比特币期货连续近月（不精确 roll，timsun 同款近似）
  - BTC-USD：现货参考价
公式：basis_annualized_pct = (F − S)/S × 365/dte × 100，
dte = CME 合约到期日 − 数据日；到期日 = 当月最后一个周五（timsun 口径）。
治理规则（timsun V1）：
  1. dte < 10 天：合约 roll 过渡期，价差失真 → 跳过
  2. |basis_pct| > 18%：大概率时区错位/数据错配 → 过滤
  3. 最后一根（当日/最新交易日）未成熟日线 → 跳过

输出 data/crypto_basis/basis.csv（观测日为 key，upsert）：
列序 [basis_pct, fut_close, spot_close]（后两列为调试用）。
本地需 HTTPS 代理：复用 yfinance_fetcher 的 ensure_yf_proxy()（设置 env，
requests 自动读取；Actions 无代理时设 YF_NO_PROXY=1 直连）。
"""

import argparse
import logging

import pandas as pd
import requests

from ..config import ROOT
from ._io import upsert_timeseries
from .yfinance_fetcher import ensure_yf_proxy

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
MIN_DTE = 10  # timsun V1：roll 过渡期阈值（天）
MAX_BASIS = 18.0  # timsun V1：失真过滤阈值（%）
COLUMNS = ["basis_pct", "fut_close", "spot_close"]


def _last_friday(d: pd.Timestamp) -> pd.Timestamp:
    """当月最后一个周五（timsun 口径：CME 月度合约到期日）。"""
    end = d.normalize() + pd.offsets.MonthEnd(0)
    return end - pd.Timedelta(days=(end.dayofweek - 4) % 7)


def _fetch_chart_close(symbol: str) -> pd.Series:
    """Yahoo chart API 日线 close（date → close，已去重排序、NaN 剔除）。"""
    url = CHART_URL.format(symbol=symbol)
    resp = requests.get(
        url,
        params={"range": "2y", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json().get("chart", {}).get("result", [])
    if not results:
        raise RuntimeError(f"Yahoo chart 返回空结果: {symbol}")
    r = results[0]
    closes = r["indicators"]["quote"][0]["close"]
    s = pd.Series(
        closes,
        index=pd.to_datetime(r["timestamp"], unit="s").normalize(),
        dtype="float64",
    )
    s = s.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = symbol
    return s


def compute_basis_series(fut: pd.Series, spot: pd.Series) -> pd.DataFrame:
    """按 timsun V1 规则计算年化基差日序列。

    fut/spot：date → close 的 Series（交易日对齐，inner join）。
    返回 index=date、列 [basis_pct, fut_close, spot_close] 的 DataFrame，
    已应用全部三条治理规则。
    """
    df = pd.concat({"fut": fut, "spot": spot}, axis=1, sort=True)
    df = df.dropna().sort_index()
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # 规则 3：最后一根（今日/最新交易日）未成熟日线跳过
    df = df.iloc[:-1]
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    dte = pd.Series([(_last_friday(d) - d).days for d in df.index], index=df.index)
    # 规则 1：roll 过渡期（dte < 10）跳过；先过滤再相除，避免 dte=0 除零
    df = df[dte >= MIN_DTE]
    basis = (df["fut"] / df["spot"] - 1.0) * 365.0 / dte.reindex(df.index) * 100.0
    # 规则 2：|basis_pct| > 18% 失真过滤
    df = df[basis.abs() <= MAX_BASIS]
    out = pd.DataFrame(
        {
            "basis_pct": basis[df.index].round(2),
            "fut_close": df["fut"].round(2),
            "spot_close": df["spot"].round(2),
        },
        index=df.index,
    )
    out.index.name = "date"
    return out


def fetch_basis_series() -> pd.DataFrame:
    """拉取 BTC=F / BTC-USD 日线并计算基差序列（含全部治理规则）。"""
    ensure_yf_proxy()
    fut = _fetch_chart_close("BTC=F")
    spot = _fetch_chart_close("BTC-USD")
    return compute_basis_series(fut, spot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="CME BTC 期货年化基差日序列（timsun V1 治理口径）"
    )
    parser.add_argument(
        "--backfill", action="store_true", help="全量覆盖（默认 upsert）"
    )
    args = parser.parse_args()

    path = ROOT / "data" / "crypto_basis" / "basis.csv"
    df = fetch_basis_series()
    upsert_timeseries(path, df, backfill=args.backfill, column_order=COLUMNS)
    if df.empty:
        logger.warning("基差序列为空（数据源无有效交易日），不写文件")
    else:
        logger.info(
            f"basis → {path} ({len(df)} 行, "
            f"{df.index[0].date()} ~ {df.index[-1].date()})"
        )
