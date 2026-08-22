"""
市场广度（ABV）：SPX 成分股在均线上方占比（timsun /assets/equities 面板）。

成分股来自 Wikipedia List of S&P 500 companies（缓存回退，复用 _wiki）；
yfinance 批量拉 2y 日线（200 日均线需要 200 天 + 产出 1 年走势）；
逐票算 close > MA(N) → 每日占比 → 只落盘派生序列（不存 500 票明细宽表，
git 友好）。data/breadth/abv.csv 观测日 upsert：date + ABV50/ABV100/ABV200。

失败容忍：退市/改名票 yfinance 自动跳过，占比按实际拉到的票计算。
"""

import logging

import pandas as pd

from ..config import ROOT

logger = logging.getLogger(__name__)

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
COMPONENTS_PATH = ROOT / "data" / "breadth" / "sp500_components.csv"
ABV_PATH = ROOT / "data" / "breadth" / "abv.csv"
WINDOWS = [50, 100, 200]
DOWNLOAD_PERIOD = "2y"  # 500 交易日：前 200 天喂均线，后 ~250 天产出 ABV200 走势


def fetch_sp500_components() -> pd.DataFrame:
    """SPX 成分（ticker/company/category=GICS Sector），网络失败回退缓存。"""
    from ._wiki import fetch_wiki_tickers

    return fetch_wiki_tickers(SP500_URL, COMPONENTS_PATH)


def download_closes(tickers: list[str]) -> pd.DataFrame:
    """yfinance 批量拉 2y 日收盘 → 宽表（index=date，列=ticker）。"""
    from .yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf

    raw = yf.download(
        tickers,
        period=DOWNLOAD_PERIOD,
        interval="1d",
        auto_adjust=False,
        threads=True,
        progress=False,
    )
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes.index.name = "date"
    return closes.sort_index()


def compute_abv(closes: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """逐票 close > MA(N) 占比 → DataFrame（index=date，列=ABV{N}，单位 %）。

    均线未满窗口的日期（前 N 天）产出 NaN；返回行按日期升序。
    """
    windows = windows or WINDOWS
    out = {}
    for n in windows:
        ma = closes.rolling(n).mean()
        # 均线未满窗口处（前 n-1 天）置 NaN，避免产出假的 0%
        above = closes.gt(ma).where(ma.notna())
        out[f"ABV{n}"] = above.mean(axis=1) * 100
    return pd.DataFrame(out)


def main() -> None:
    components = fetch_sp500_components()
    closes = download_closes(components["ticker"].tolist())
    logger.info(
        f"SPX 成分 {len(components)} 只，拉取到 {len(closes.columns)} 只有效收盘"
    )

    abv = compute_abv(closes)
    if abv.empty or len(abv) < 200:
        logger.warning(f"ABV 数据不足（{len(abv)} 行），跳过写盘")
        return

    # 首日回填整条历史（abv.csv 只有最新行时，先在本地补全 1 年走势——
    # yfinance 2y 日线即可产出 ABV200 序列；之后每日只追加最新行）
    from ._io import upsert_rows

    abv = abv.dropna()
    if not abv.empty:
        existing = pd.read_csv(ABV_PATH) if ABV_PATH.exists() else pd.DataFrame()
        seed = len(existing) < 200
        rows = abv.reset_index() if seed else abv.iloc[-1:].reset_index()
        rows.columns = ["date", "ABV50", "ABV100", "ABV200"]
        rows["date"] = pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d")
        upsert_rows(ABV_PATH, rows, subset=["date"], sort_by=["date"])
        latest = abv.iloc[-1].round(2).to_dict()
        logger.info(
            "ABV → %s: %s（%s）",
            ABV_PATH,
            latest,
            f"回填 {len(rows)} 行" if seed else "追加 1 行",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
