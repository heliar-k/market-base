"""Fetch COT (Commitments of Traders) from CFTC official source.

CFTC 官方历史数据免费公开（Barchart 等展示方均为转发此数据）：
- 商品期货（disaggregated）:  https://www.cftc.gov/files/dea/history/fut_disagg_txt_{YEAR}.zip
- 金融期货（financial/TFF）:  https://www.cftc.gov/files/dea/history/fut_fin_txt_{YEAR}.zip
每年一个 zip 含全年周度报告（周二数据、周五发布）。

写入 data/cot/cot.csv（观测日 = 报告日期，upsert 宽表）：
列 {SYM}_{METRIC}：
- 商品（disaggregated）: OI / PROD_L / PROD_S / SWAP_L / SWAP_S / MM_L / MM_S
  （PROD=生产商/商户，SWAP=掉期商，MM=管理资金/投机）
- 金融（TFF）: OI / DEALER_L / DEALER_S / ASSET_L / ASSET_S / HEDGE_L / HEDGE_S
  （DEALER=做市商，ASSET=资管，HEDGE=对冲基金）
L/S=多/空持仓。

用法:
    uv run python -m src.fetchers.cot_fetcher              # 当年+去年
    uv run python -m src.fetchers.cot_fetcher --years 2025  # 指定年份
"""

import argparse
import io
import logging
import zipfile
from datetime import date

import pandas as pd
import requests

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

# 品种 → (CFTC 市场名正则, 报告类型)；YM(道指) 不在 CFTC COT 报告中（2024-26 均无）
# 正则以 " - "（市场名 - 交易所）锚定，避免前缀误匹配（如 E-MINI S&P 500 MIDCAP）
COT_MARKETS: dict[str, tuple[str, str]] = {
    "GC": (r"^GOLD - COMMODITY EXCHANGE", "disagg"),
    "SI": (r"^SILVER - COMMODITY EXCHANGE", "disagg"),
    "HG": (r"^COPPER- #1 - ", "disagg"),
    "CL": (r"^(CRUDE OIL, LIGHT SWEET-WTI - NEW YORK|WTI-PHYSICAL)", "disagg"),
    "NG": (r"^HENRY HUB - NEW YORK", "disagg"),
    "ES": (r"^E-MINI S&P 500 - ", "fin"),
    "NQ": (r"^(E-MINI )?NASDAQ-100( Consolidated)? - ", "fin"),
    "RTY": (r"^(E-)?MINI RUSSELL 2000 - |^RUSSELL E-MINI - ", "fin"),
    "ZQ": (r"^(30-DAY )?FED FUNDS - ", "fin"),
    # ── timsun 持仓追踪扩展（2026-08）──
    "VX": (r"^VIX FUTURES - CBOE FUTURES EXCHANGE", "fin"),
    "ZF": (r"^UST 5Y NOTE - CHICAGO BOARD OF TRADE", "fin"),
    "ZN": (r"^UST 10Y NOTE - CHICAGO BOARD OF TRADE", "fin"),
    "ZB": (r"^UST BOND - CHICAGO BOARD OF TRADE", "fin"),
    "EUR": (r"^EURO FX - CHICAGO MERCANTILE EXCHANGE", "fin"),
    "JPY": (r"^JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE", "fin"),
    # 加密（timsun 衍生品页 CME 机构头寸信号；CME 比特币期货，TFF 报告）
    "BTC": (r"^BITCOIN - CHICAGO MERCANTILE EXCHANGE", "fin"),
    # 注：DXY（ICE 美元指数）不在 CFTC COT 报告中（2025/2026 均无），无源可拉
}

URL = "https://www.cftc.gov/files/dea/history/fut_{type}_txt_{year}.zip"

# 目标列（按报告类型的市场列名 → 输出指标名）
_METRICS = {
    "disagg": {
        "Open_Interest_All": "OI",
        "Prod_Merc_Positions_Long_All": "PROD_L",
        "Prod_Merc_Positions_Short_All": "PROD_S",
        "Swap_Positions_Long_All": "SWAP_L",
        "Swap__Positions_Short_All": "SWAP_S",
        "M_Money_Positions_Long_All": "MM_L",
        "M_Money_Positions_Short_All": "MM_S",
    },
    # TFF 金融报告分类：Dealer(做市商)/Asset Manager(资管)/Leveraged Funds(对冲基金)
    "fin": {
        "Open_Interest_All": "OI",
        "Dealer_Positions_Long_All": "DEALER_L",
        "Dealer_Positions_Short_All": "DEALER_S",
        "Asset_Mgr_Positions_Long_All": "ASSET_L",
        "Asset_Mgr_Positions_Short_All": "ASSET_S",
        "Lev_Money_Positions_Long_All": "HEDGE_L",
        "Lev_Money_Positions_Short_All": "HEDGE_S",
    },
}


def _download_year(year: int, report_type: str) -> pd.DataFrame:
    """下载某年某类型 COT zip 并解析为 DataFrame（全部市场）。"""
    url = URL.format(type=report_type, year=year)
    resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    name = zf.namelist()[0]
    df = pd.read_csv(zf.open(name))
    df["Report_Date_as_YYYY-MM-DD"] = pd.to_datetime(
        df["Report_Date_as_YYYY-MM-DD"]
    ).dt.strftime("%Y-%m-%d")
    return df


def fetch_cot(years: list[int] | None = None) -> pd.DataFrame:
    """拉取 COT 宽表：index=报告日期，列={SYM}_{METRIC}。

    单年单类型失败仅告警跳过（如今年数据尚未生成）。
    """
    years = years or [date.today().year, date.today().year - 1]
    by_sym: dict[str, list[pd.DataFrame]] = {}
    for year in years:
        for report_type in ("disagg", "fin"):
            try:
                df = _download_year(year, report_type)
            except Exception as e:
                logger.warning("CFTC %d %s 下载失败: %s", year, report_type, e)
                continue
            for sym, (pat, rtype) in COT_MARKETS.items():
                if rtype != report_type:
                    continue
                market = df[df["Market_and_Exchange_Names"].str.match(pat, case=False)]
                if market.empty:
                    logger.warning("COT %s: %s 无匹配市场", year, sym)
                    continue
                # 同一年内市场名可能微调，取最近一周匹配行的列
                metrics = _METRICS[report_type]
                cols = {
                    f"{sym}_{out}": pd.to_numeric(
                        market[c].astype(str).str.replace(",", ""), errors="coerce"
                    )
                    for c, out in metrics.items()
                }
                cols["report_date"] = market["Report_Date_as_YYYY-MM-DD"]
                by_sym.setdefault(sym, []).append(
                    pd.DataFrame(cols).groupby("report_date").first()
                )
    frames = []
    for sym, parts in by_sym.items():
        f = pd.concat(parts)  # 年份纵向合并（同列名）
        frames.append(f[~f.index.duplicated(keep="last")].sort_index())
    if not frames:
        return pd.DataFrame()
    # 品种间按报告日横向对齐
    return pd.concat(frames, axis=1).sort_index()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="CFTC COT 持仓报告拉取")
    parser.add_argument(
        "--years", default="", help="年份列表（逗号分隔，默认当年+去年）"
    )
    parser.add_argument("--backfill", action="store_true", help="全量覆盖")
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(",") if y.strip()] or None
    df = fetch_cot(years)
    if df.empty:
        print("无数据")
        raise SystemExit(1)

    path = ROOT / "data" / "cot" / "cot.csv"
    upsert_timeseries(path, df, backfill=args.backfill)
    latest = df.index[-1]
    n_markets = len({c.split("_")[0] for c in df.columns})
    print(f"COT → {path}（{len(df)} 个报告日, {n_markets} 个品种, 最新 {latest}）")
    print(f"样例: {df.iloc[-1].dropna().head(8).to_dict()}")
