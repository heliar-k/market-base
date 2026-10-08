"""贵金属 ETF 官方持仓抓取（GLD 金 + SLV 银，日度，免费官方源）。

数据源（调研 2026-10）：
- GLD（SPDR Gold Shares，State Street）——官方 historical-archive API，
  xlsx 含逐日 Tonnes of Gold 持仓（2004 至今），curl + UA 直连即可（无 Cloudflare）：
    https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&exchange=NYSE&lang=en
- SLV（iShares Silver Trust，BlackRock）——varnish 文档接口返回
  SpreadsheetML（Excel 2003 XML），含逐日 NAV + Shares Outstanding（2006 至今），
  无金属吨位行，按每份额盎司系数换算吨位（系数年漂移 <0.5%，来自费用摊销，
  用最新常数估算历史偏差可忽略）。

写入 data/commodities/etf_holdings.csv（观测日 upsert，全量历史覆盖合并）。
用途：大宗商品页「贵金属 ETF 资金流」小节——持仓变化即实物 ETF 流入流出（吨）。
"""

from __future__ import annotations

import io
import logging
import math
import re

import pandas as pd
import requests

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

GLD_URL = (
    "https://api.spdrgoldshares.com/api/v1/historical-archive"
    "?product=gld&exchange=NYSE&lang=en"
)
SLV_URL = (
    "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/"
    "get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares"
    "&locale=en_US&portfolioId=239855&component=fundDownload&userType=individual"
)
# SLV 每份额对应白银盎司：每次运行从官方产品页内嵌 JSON 取当日持仓吨位反推
# （随费用摊销自动校准），页面解析失败时用回退常数（2026-10 实测）
SLV_OZ_PER_SHARE_FALLBACK = 0.903
OZ_PER_TONNE = 32150.7465
SLV_PAGE_URL = "https://www.ishares.com/us/products/239855/ishares-silver-trust-fund"

_OUT = ROOT / "data" / "commodities" / "etf_holdings.csv"


def fetch_gld() -> pd.DataFrame:
    """拉 GLD 官方历史档案 xlsx → (date, gld_tonnes)。

    文件首 sheet 是免责声明，数据在 "US GLD Historical Archive" sheet：
    Date / Closing Price / ... / Tonnes of Gold / Total Net Asset Value。
    """
    r = requests.get(GLD_URL, timeout=90, headers={"User-Agent": UA})
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise RuntimeError(
            f"GLD 返回非 xlsx（{r.headers.get('content-type')}），接口可能改版"
        )
    df = pd.read_excel(io.BytesIO(r.content), sheet_name="US GLD Historical Archive")
    # Date / Tonnes 列都混有 "US Holiday" 文本行，两侧 coerce 后丢弃
    ton = pd.to_numeric(df["Tonnes of Gold"], errors="coerce")
    idx = pd.to_datetime(df["Date"], format="%d-%b-%Y", errors="coerce")
    ok = ton.notna() & idx.notna()
    out = pd.DataFrame({"gld_tonnes": ton[ok].round(2).values}, index=idx[ok])
    out.index.name = "date"
    return out


def _slv_oz_per_share(shares_latest: float) -> float:
    """从 SLV 产品页内嵌 JSON 取当日持仓吨位，反推每份额盎斯数。"""
    try:
        r = requests.get(SLV_PAGE_URL, timeout=60, headers={"User-Agent": UA})
        r.raise_for_status()
        m = re.search(
            r'formattedValue&quot;:&quot;([\d,.]+)&quot;.{0,200}?"name&quot;:&quot;tonnes&quot;',
            r.text,
            re.S,
        )
        if m:
            tonnes = float(m.group(1).replace(",", ""))
            if tonnes > 0:
                oz = tonnes * OZ_PER_TONNE / shares_latest
                logger.info("SLV 当日持仓 %.1f t → %.6f oz/股", tonnes, oz)
                return oz
    except Exception as e:  # noqa: BLE001 —— 页面失败用回退常数
        logger.warning("SLV 产品页取吨位失败（用回退系数）：%s", e)
    return SLV_OZ_PER_SHARE_FALLBACK


def fetch_slv() -> pd.DataFrame:
    """拉 SLV varnish 文档（SpreadsheetML）→ (date, slv_shares_mn, slv_tonnes_est)。

    pandas 不支持 Excel 2003 XML 格式，用正则解析 <ss:Row>（文件结构稳定：
    Disclaimers / Historical / Performance 三个 sheet，Historical 首行表头）。
    """
    r = requests.get(SLV_URL, timeout=90, headers={"User-Agent": UA})
    r.raise_for_status()
    xml = r.content.decode("utf-8", errors="ignore")
    # 只取 Historical sheet，避免免责/业绩 sheet 里的数字混入
    hist = re.search(r'<ss:Worksheet ss:Name="Historical".*?</ss:Worksheet>', xml, re.S)
    if not hist:
        raise RuntimeError("SLV SpreadsheetML 缺 Historical sheet，接口可能改版")
    rows = re.findall(r"<ss:Row>(.*?)</ss:Row>", hist.group(0), re.S)
    recs: list[tuple[str, float]] = []
    for row in rows:
        cells = re.findall(r"<ss:Data[^>]*>([^<]*)</ss:Data>", row)
        if len(cells) < 4:
            continue
        try:
            shares = float(cells[3].replace(",", ""))
        except ValueError:
            continue
        if shares <= 0:
            continue
        recs.append((cells[0], shares))
    idx = pd.to_datetime([d for d, _ in recs], format="%b %d, %Y")
    shares = pd.Series([s for _, s in recs], index=idx, name="slv_shares_mn") / 1e6
    shares = shares.sort_index()  # 源文件日期倒序，统一成正序
    oz_per_share = _slv_oz_per_share(float(shares.iloc[-1]) * 1e6)
    out = pd.DataFrame(
        {
            "slv_shares_mn": shares.round(2),
            "slv_tonnes_est": (shares * 1e6 * oz_per_share / OZ_PER_TONNE).round(1),
        }
    )
    out.index.name = "date"
    return out


def run() -> pd.DataFrame:
    """全量拉取 + upsert，返回合并后的 DataFrame。任一源失败不覆盖已有数据。"""
    frames = []
    errors = []
    for name, fn in (("GLD", fetch_gld), ("SLV", fetch_slv)):
        try:
            df = fn()
            logger.info(
                "%s %d 行（%s → %s）",
                name,
                len(df),
                df.index[0].date(),
                df.index[-1].date(),
            )
            frames.append(df)
        except Exception as e:  # noqa: BLE001 —— 单源挂掉不写坏已有数据
            errors.append(f"{name}: {type(e).__name__} {e}")
    if not frames:
        raise RuntimeError("GLD/SLV 全部拉取失败：" + "; ".join(errors))
    if errors:
        logger.warning("部分源失败（只用成功的源 upsert）：%s", "; ".join(errors))
    merged = pd.concat(frames, axis=1, sort=True)
    # 两源日期网格不同（美 holiday 一致，但缺失日保留旧值），upsert 会自动对齐合并
    if "gld_tonnes" in merged.columns and math.isnan(merged["gld_tonnes"].iloc[-1]):
        merged["gld_tonnes"] = merged["gld_tonnes"].ffill()
    upsert_timeseries(_OUT, merged)
    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = run()
    print(
        f"etf_holdings.csv: {len(df)} 行"
        f"（{df.index[0].date()} → {df.index[-1].date()}）"
    )
    print(df.tail(3).to_string())
