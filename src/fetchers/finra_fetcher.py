"""Fetch daily short sale volume from FINRA Reg SHO.

数据源: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
- T+1 免费公开，无需 key；周末/假日无文件（404）
- CNMS = 合并 NMS 全市场文件，pipe 分隔，首行表头
- 实测（2026-08）：中国大陆 IP 直连被 WAF 拦（307），走 SOCKS5 代理可通；
  GitHub Actions（美国 IP）直连即可 → 直连优先，失败 fallback 代理
  （环境变量 YF_NO_PROXY=1 时跳过代理，同 yfinance_fetcher 约定）
- 注：文件中 ShortVolume/TotalVolume 偶见小数（实测 316055.595634，
  疑为 TRF 加权口径），按浮点原样存储不取整

输出: data/short_selling/finra_daily.csv（宽表，观测日 = 交易日期，upsert）
列 {SYM}_short_ratio = ShortVolume / TotalVolume；{SYM}_short_vol = ShortVolume

用法:
    uv run python -m src.fetchers.finra_fetcher
    uv run python -m src.fetchers.finra_fetcher --symbols AAPL,TSM
    uv run python -m src.fetchers.finra_fetcher --backfill
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from datetime import date, timedelta

import pandas as pd
import requests

from ..config import ROOT, STOCKS, config
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"

# 直连被 WAF 拦时的 fallback 代理（.env 配置；YF_NO_PROXY=1 跳过，Actions 直连）
# 注：socks5 与 socks5h 均可（cdn.finra.org DNS 本地解析正常），沿用 config 现值
_PROXY_URL = (
    ""
    if os.environ.get("YF_NO_PROXY")
    else (config.https_proxy or "socks5h://127.0.0.1:7890")
)

_SESSION = requests.Session()
_SESSION.trust_env = False  # 代理只走显式 proxies 参数，不读环境变量

# 直连可用性记忆：首次被 WAF 拦后本进程内只用代理（避免每个请求都等 WAF 错误页超时）
_DIRECT_OK = True


def _request(method: str, url: str, timeout: float) -> requests.Response | None:
    """单次请求：直连优先（失败记忆），fallback 代理；非 200 返回 None。

    stream=True + 即时 close：被 WAF 拦时（307/403）不下载拦截页 body
    （默认模式会等 body 读完，每个请求白等 15-25s）；403 视为缺失
    （cdn.finra.org 对不存在的路径实测返回 403 而非 404）。
    """
    global _DIRECT_OK
    attempts = [None] if _DIRECT_OK else []
    if _PROXY_URL:
        attempts.append({"https": _PROXY_URL, "http": _PROXY_URL})
    for proxies in attempts:
        try:
            r = _SESSION.request(
                method,
                url,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            if r.status_code in (301, 302, 303, 307, 308) and proxies is None:
                r.close()
                _DIRECT_OK = False  # 直连被 WAF 重定向 → 后续只用代理
                continue
            if r.status_code == 200:
                return r
            r.close()
            if r.status_code in (403, 404):
                logger.debug("无文件: %s", url)
                return None
        except requests.RequestException as e:
            if proxies is None:
                _DIRECT_OK = False
            logger.debug("请求失败 %s: %s", url, e)
            continue
    logger.warning("下载失败（直连+代理均不可达）: %s", url)
    return None


def _get(url: str) -> str | None:
    """下载文件文本。"""
    r = _request("GET", url, 30)
    return r.text if r is not None else None


def _exists(d: date) -> bool:
    """HEAD 探测某日文件是否存在（探测阶段用，避免下载整个文件）。"""
    return _request("HEAD", URL.format(date=d.strftime("%Y%m%d")), 15) is not None


def _parse(text: str, symbols: set[str]) -> pd.DataFrame:
    """解析单日文件 → 宽表（index=观测日，列 {SYM}_short_ratio/_short_vol）。"""
    df = pd.read_csv(io.StringIO(text), sep="|")
    df = df[df["Symbol"].isin(symbols)]
    if df.empty:
        return pd.DataFrame()
    for c in ("ShortVolume", "TotalVolume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    d = pd.to_datetime(df["Date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["date"] = d
    ratio = df["ShortVolume"] / df["TotalVolume"]
    wide = pd.concat(
        [
            pd.DataFrame(
                {"date": df["date"], "Symbol": df["Symbol"], "short_ratio": ratio}
            )
            .pivot(index="date", columns="Symbol", values="short_ratio")
            .add_suffix("_short_ratio"),
            df[["date", "Symbol", "ShortVolume"]]
            .pivot(index="date", columns="Symbol", values="ShortVolume")
            .add_suffix("_short_vol"),
        ],
        axis=1,
    )
    return wide


def backfill_dates(max_days: int = 400) -> list[date]:
    """探测历史深度：以最近交易日为锚，每 7 天向前探测，连续 2 桶无文件视为边界。

    锚点避免 7 天步进全落在周末（今天为周末时所有桶都是 404）；
    单桶错过（假日）容忍，连续两桶才截断；单次最多 400 天，可重复续跑。
    """
    today = date.today()
    anchor = next(
        (
            today - timedelta(days=i)
            for i in range(10)
            if _exists(today - timedelta(days=i))
        ),
        None,
    )
    if anchor is None:
        return []
    oldest = anchor
    misses = 0
    for offset in range(7, max_days, 7):
        d = anchor - timedelta(days=offset)
        if _exists(d):
            oldest = d
            misses = 0
        else:
            misses += 1
            if misses >= 2:
                break
    return [oldest + timedelta(days=i) for i in range((anchor - oldest).days + 1)]


def fetch_finra(dates: list[date], symbols: set[str]) -> pd.DataFrame:
    """拉取指定日期的沽空数据（404 静默跳过），合并为宽表。"""
    frames = []
    for d in sorted(dates):
        text = _get(URL.format(date=d.strftime("%Y%m%d")))
        if text is None:
            continue
        frames.append(_parse(text, symbols))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames)


def main() -> None:
    parser = argparse.ArgumentParser(description="FINRA 每日沽空量拉取")
    parser.add_argument("--symbols", help="逗号分隔标的（默认配置全部股票）")
    parser.add_argument(
        "--backfill", action="store_true", help="探测历史深度并全量回填"
    )
    parser.add_argument("--days", type=int, default=7, help="近 N 天增量（默认 7）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    symbols = (
        {s.strip().upper() for s in args.symbols.split(",")}
        if args.symbols
        else {sc.name for sc in STOCKS}
    )
    if args.backfill:
        dates = backfill_dates()
        logger.info("回填 %d 天", len(dates))
    else:
        dates = [date.today() - timedelta(days=i) for i in range(args.days)]

    df = fetch_finra(dates, symbols)
    if df.empty:
        print("无数据（周末/假日或网络失败）")
        raise SystemExit(1)
    path = ROOT / "data" / "short_selling" / "finra_daily.csv"
    upsert_timeseries(path, df)
    latest = df.index[-1]
    print(f"FINRA → {path}（{len(df)} 个交易日, 最新 {latest}）")
    print(f"样例: {df.iloc[-1].dropna().head(6).to_dict()}")


if __name__ == "__main__":
    main()
