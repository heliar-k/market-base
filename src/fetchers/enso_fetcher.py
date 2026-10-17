"""Fetch NOAA CPC ENSO indicators: ONI index + monthly Diagnostic Discussion.

数据源（NOAA CPC 官方、免费、免 Key，直连即可；trust_env=False 绕过本地代理）:
- ONI 指数（Niño-3.4 区 3 个月滑动海温距平，月频）:
    https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
  格式: 空白分隔文本，表头 `SEAS  YR   TOTAL   ANOM`，ANOM 即 ONI（°C）。
  SEAS 为 3 个月滑动窗口缩写（DJF/JFM/...），映射到窗口中心月份作日期索引。
- ENSO Diagnostic Discussion（月度诊断研报，约每月第二个周四发布）:
    https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml

输出:
- data/macro/enso.csv — index=季节中心月（YYYY-MM-01），列 season/total/oni（upsert）
- data/macro/enso_discussion.json — 最新一期诊断研报（覆盖写）

用法:
    uv run python -m src.fetchers.enso_fetcher
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape

import pandas as pd
import requests

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DISC_URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"

OUT_CSV = ROOT / "data" / "macro" / "enso.csv"
OUT_JSON = ROOT / "data" / "macro" / "enso_discussion.json"

# 3 个月滑动窗口缩写 → 中心月份（DJF 跨年，以窗口中点 1 月计）
SEAS_TO_MONTH: dict[str, int] = {
    "DJF": 1,
    "JFM": 2,
    "FMA": 3,
    "MAM": 4,
    "AMJ": 5,
    "MJJ": 6,
    "JJA": 7,
    "JAS": 8,
    "ASO": 9,
    "SON": 10,
    "OND": 11,
    "NDJ": 12,
}

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# 直连会话：NOAA 无反爬，忽略 .env 中的 SOCKS5 代理（同 refunding_fetcher）
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (research script; +https://github.com/)"})


def parse_oni_ascii(text: str) -> pd.DataFrame:
    """解析 ONI 固定空白分隔文本 → DataFrame（index=季节中心月，列 season/total/oni）。

    样例行: `  JAS 2026  29.12   2.16`（表头 ` SEAS  YR   TOTAL   ANOM`）。
    """
    rows: list[tuple[str, int, float, float]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] not in SEAS_TO_MONTH:
            continue  # 表头/空行
        season, yr, total, anom = parts[0], int(parts[1]), float(parts[2]), float(parts[3])
        rows.append((season, yr, total, anom))
    if not rows:
        raise ValueError("ONI 文本解析为空（格式可能已变更）")
    df = pd.DataFrame(rows, columns=["season", "year", "total", "oni"])
    month = df["season"].map(SEAS_TO_MONTH)
    df.index = pd.to_datetime(df["year"].astype(str) + "-" + month.astype(str).str.zfill(2) + "-01")
    return df[["season", "total", "oni"]]


def _strip_html(html: str) -> str:
    """标签剥离 + 实体解码 + 空白归一（&nbsp; → 空格）。"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_enso_discussion(html: str) -> dict:
    """解析 ENSO Diagnostic Discussion 页面 → 摘要字典。

    页面结构（2026-10 实测）: 发布日期与机构名相邻、状态徽章含已知枚举值、
    摘要首句在 `Synopsis:` 后的第一个 <strong> 块、正文止于署名样板句。
    """
    text = _strip_html(html)

    # 发布日期: "issued by CLIMATE PREDICTION CENTER/NCEP/NWS 8 October 2026"
    m = re.search(r"CLIMATE PREDICTION CENTER/NCEP/NWS\s+(\d{1,2}\s+([A-Za-z]+)\s+(\d{4}))", text)
    issued_date = ""
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            issued_date = f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1).split()[0]):02d}"

    # 警报状态: 已知枚举（Advisory/Watch/Not Active），避免匹配到后续句子
    m = re.search(
        r"ENSO Alert System Status:\s*"
        r"(El Ni\S+ Advisory|La Ni\S+ Advisory|El Ni\S+ Watch|La Ni\S+ Watch|Not Active|"
        r"Final Advisory)",
        text,
    )
    status = m.group(1) if m else ""

    # 核心结论句: `Synopsis:` 后的第一个 <strong> 块（官方加粗总结句）
    headline = ""
    m = re.search(r"Synopsis:.*?<strong>\s*(.*?)\s*</strong>", html, re.S)
    if m:
        headline = _strip_html(m.group(1))

    # 摘要正文: Synopsis 起到 NOAA 署名样板句止，保留前 900 字符
    excerpt = ""
    m = re.search(r"Synopsis:(.*?)This discussion is a consolidated effort", html, re.S)
    if m:
        excerpt = _strip_html(m.group(1))[:900]

    # 下次发布日
    m = re.search(r"next ENSO Diagnostics Discussion is scheduled for\s+([^.]+?)\.", text)
    next_release = m.group(1).strip() if m else ""

    return {
        "as_of": issued_date,
        "status": status,
        "headline": headline,
        "excerpt": excerpt,
        "next_release": next_release,
        "url": DISC_URL,
    }


def fetch_enso() -> None:
    """拉取 ONI + 诊断研报并写盘。单源失败抛异常（不静默），由调用方记失败列表。"""
    logger.info("拉取 ONI: %s", ONI_URL)
    resp = _SESSION.get(ONI_URL, timeout=30)
    resp.raise_for_status()
    oni = parse_oni_ascii(resp.text)
    upsert_timeseries(OUT_CSV, oni)
    logger.info(
        "ONI → %s（%d 期，最新 %s %.2f°C）",
        OUT_CSV,
        len(oni),
        oni.index[-1].date(),
        oni["oni"].iloc[-1],
    )

    logger.info("拉取 ENSO Diagnostic Discussion: %s", DISC_URL)
    resp = _SESSION.get(DISC_URL, timeout=30)
    resp.raise_for_status()
    disc = parse_enso_discussion(resp.text)
    if not disc["as_of"] or not disc["status"]:
        raise ValueError("ENSO 诊断研报解析不完整（issued date / status 缺失）")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(disc, ensure_ascii=False, indent=2) + "\n")
    logger.info("Discussion → %s（%s · %s）", OUT_JSON, disc["as_of"], disc["status"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    fetch_enso()
