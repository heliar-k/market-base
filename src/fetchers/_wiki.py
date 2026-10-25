"""Wikipedia 成分股表解析（NDX / S&P 500 共用）。

解析第一个 wikitable（列0=ticker、列1=公司名、行业列按表头关键字定位），
网络失败回退本地缓存（缓存文件由调用方指定）。

行业列必须按表头找，不能写死列号：2026-10 Wikipedia 给 NASDAQ-100 表插了
ISIN 列（Ticker/Company/ISIN/ICB Industry/ICB Subsector），写死 cells[2]
把 ISIN 当行业灌进了 ndx_components.csv 与 ndx_targets.csv。
"""

import logging
import re
from html import unescape
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
_INDUSTRY_HEADER = re.compile(r"industry|sector", re.I)
_ISIN = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")  # 行业列误取成 ISIN 时拒收
_DEFAULT_CATEGORY_COL = 2  # 表头未识别时沿用旧假设：列2 = 行业


def _cell_texts(tr_html: str) -> list[str]:
    """一个 <tr> 里的单元格纯文本（已剔标签）。"""
    return [
        unescape(re.sub(r"<[^>]+>", "", c)).strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.S)
    ]


def _category_col(table_html: str) -> int:
    """行业列位置：前几行里首个含 industry/sector 的单元格所在列号。

    扫前几行而非只扫首行：wikitable 常有合并标题行（<th colspan=N>），
    只看首行会漏掉表头并静默退回列号假设。
    """
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)[:3]:
        for i, c in enumerate(_cell_texts(tr)):
            if _INDUSTRY_HEADER.search(c):
                return i
    logger.warning(f"成分表未找到 industry/sector 列，回退列号 {_DEFAULT_CATEGORY_COL}")
    return _DEFAULT_CATEGORY_COL


def fetch_wiki_tickers(url: str, cache_path: Path) -> pd.DataFrame:
    """抓 Wikipedia 成分表 → DataFrame(ticker, company, category)。

    网络失败时回退读 cache_path（上次成功的成分）；两者都失败时抛出。
    """
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": _UA})
        resp.raise_for_status()
        m = re.search(
            r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
            resp.text,
            re.S,
        )
        if not m:
            raise ValueError("成分表未找到")
        col = _category_col(m.group(1))
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
            cells = _cell_texts(tr)
            # 要同时有公司列（1）和行业列（col）才收这一行
            if (
                len(cells) > max(col, 1)
                and cells[0] not in ("Ticker", "Symbol", "")
                and re.match(r"^[A-Z][A-Z0-9.\-]{0,4}$", cells[0])
                and not _ISIN.match(cells[col])
            ):
                rows.append({"ticker": cells[0], "company": cells[1], "category": cells[col]})
        if not rows:
            raise ValueError("成分表解析为空")
        df = pd.DataFrame(rows).drop_duplicates(subset="ticker")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        return df
    except Exception as e:
        logger.warning(f"Wikipedia 成分拉取失败（回退缓存）: {e}")
        if cache_path.exists():
            return pd.read_csv(cache_path)
        raise
