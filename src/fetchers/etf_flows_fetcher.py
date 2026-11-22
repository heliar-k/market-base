"""Farside BTC 现货 ETF 资金流（timsun 衍生品页 ETF FLOWS 数据源）。

Farside 页面是服务端渲染的静态 HTML 表，Cloudflare 只查 TLS 指纹 —— curl_cffi 以 chrome
指纹直连就拿到 200 与整张表（2026-11 实测 866KB）。经 Jina Reader 时会被 r.jina.ai 对
Actions 出口 IP 的 401 掐断（11-14 起约一半班次失败）且多烧一份 token，故直接取。

数据：12 ETF + Total，2024-01-11 起，单位 M USD（负值 = 净流出）；
Farside 每日 ~21:00 UTC 更新。

写入 data/etf_flows/etf_flows.csv（观测日 upsert 宽表）。
列序: date + IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC Total。

用法:
    uv run python -m src.fetchers.etf_flows_fetcher           # 拉全量并 upsert
    uv run python -m src.fetchers.etf_flows_fetcher --backfill  # 全量覆盖
"""

from __future__ import annotations

import argparse
import logging
import re
from html import unescape

import pandas as pd
from curl_cffi import requests as cffi_requests

from ..config import ROOT
from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

PAGE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"

# timsun 面板列序（Farside 表头同序）；Total = 12 ETF 当日净流入合计
COLUMNS = [
    "IBIT",
    "FBTC",
    "BITB",
    "ARKB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "MSBT",
    "GBTC",
    "BTC",
    "Total",
]

OUT = ROOT / "data" / "etf_flows" / "etf_flows.csv"

_DATE_RE = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$")
_NUM_RE = re.compile(r"^([\-\(]?)(\d[\d,]*\.?\d*)(\)?)$")
_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr.*?</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _to_float(tok: str) -> float | None:
    """Farside 数值 token：'-'=无数据；'(95.1)'=负值。"""
    tok = tok.strip()
    if tok in ("", "-", "N/A", "—", "–"):
        return None
    m = _NUM_RE.match(tok)
    if not m:
        return None
    sign = -1.0 if m.group(1) in ("(", "-") else 1.0
    return sign * float(m.group(2).replace(",", ""))


def _cells(tr_html: str) -> list[str]:
    """一个 <tr> 的单元格纯文本（与 _wiki.py 同一套正则，不引 HTML 解析依赖）。"""
    return [unescape(_TAG_RE.sub("", c)).strip() for c in _CELL_RE.findall(tr_html)]


def parse_farside(html: str, columns: list[str] | None = None) -> pd.DataFrame:
    """解析 Farside 页面第一张 <table>（M USD）。

    columns 缺省用 BTC 页 COLUMNS；ETH 页传自己的列清单。
    只认首列是日期的行：页头的 Fee/Seed 行、页尾的 Total（累计）行自然跳过。
    """
    columns = columns or COLUMNS
    table = _TABLE_RE.search(html)
    if table is None:
        return pd.DataFrame()
    records = []
    header_checked = False
    for tr in _ROW_RE.findall(table.group(0)):
        cells = _cells(tr)
        if len(cells) < 2:
            continue
        if not header_checked:
            header_checked = True
            page_cols = [c for c in cells[1:] if c]
            # 表头（表的第一行）与内置列序不一致 → 后续按位置取值会把邻居列写进 Total，就地报错
            if page_cols != columns:
                raise RuntimeError(
                    f"Farside 表头与预期不一致: {page_cols[:5]}…（页面可能加了/改了标的）"
                )
            continue
        if not _DATE_RE.match(cells[0]):
            continue
        nums = [_to_float(v) for v in cells[1 : 1 + len(columns)]]
        nums += [None] * (len(columns) - len(nums))
        d = pd.to_datetime(cells[0], format="%d %b %Y").strftime("%Y-%m-%d")
        records.append({"date": d, **dict(zip(columns, nums))})
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("date")
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_flows(url: str = PAGE_URL, columns: list[str] | None = None) -> pd.DataFrame:
    """直连 Farside 拉全表并解析（HTTPS_PROXY 由 curl_cffi 自己读，本地不用另配）。"""
    resp = cffi_requests.Session(impersonate="chrome", timeout=60).get(url)
    resp.raise_for_status()
    df = parse_farside(resp.text, columns)
    if df.empty:
        raise RuntimeError(f"Farside 解析为空（响应 {len(resp.text)} 字符）")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="全量覆盖旧文件")
    args = parser.parse_args()

    df = fetch_flows()
    upsert_timeseries(OUT, df, backfill=args.backfill, column_order=COLUMNS)
    logger.info(
        "etf_flows upsert → %s 行（%s 起 / %s 止）",
        OUT,
        df.index[0],
        df.index[-1],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
