"""Wikipedia 成分股表解析（NDX / S&P 500 共用）。

解析第一个 wikitable（列0=ticker、列1=公司名、列2=行业/板块），
网络失败回退本地缓存（缓存文件由调用方指定）。
"""

import logging
import re
from html import unescape
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


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
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
            cells = [
                unescape(re.sub(r"<[^>]+>", "", c)).strip()
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
            ]
            if (
                len(cells) >= 3
                and cells[0] not in ("Ticker", "Symbol", "")
                and re.match(r"^[A-Z][A-Z0-9.\-]{0,4}$", cells[0])
            ):
                rows.append(
                    {"ticker": cells[0], "company": cells[1], "category": cells[2]}
                )
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
