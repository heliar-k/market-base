"""Farside BTC 现货 ETF 资金流（timsun 衍生品页 ETF FLOWS 数据源）。

Farside 官网直连 403（Cloudflare 反爬），经 Jina Reader (r.jina.ai) 抓取
Markdown 渲染文本（timsun 同款做法；免费版 ~20 RPM，每日一次足够）。

数据：12 ETF + Total，2024-01-11 起，单位 M USD（负值 = 净流出）；
Farside 每日 ~21:00 UTC 更新。另含累计持仓量（Holdings）列，本期不解析。

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

import pandas as pd

from ..config import ROOT
from ._io import upsert_timeseries
from .jina_reader import jina_fetch

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
_NUM_RE = re.compile(r"^([\-\(]?)([\d,]+\.?\d*)(\)?)$")


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


def _parse_table(content: str) -> pd.DataFrame:
    """解析 Jina Markdown 管道表（2026-08 起 Jina 把 Farside 表渲染成 | a | b | 形式）：
    每行 | 日期 | 13 值 |，列序即 COLUMNS（与 Farside 表头一致）。"""
    records = []
    for ln in content.splitlines():
        if "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 3 or not _DATE_RE.match(cells[0]):
            continue
        nums = [_to_float(v) for v in cells[1 : 1 + len(COLUMNS)]]
        nums += [None] * (len(COLUMNS) - len(nums))
        d = pd.to_datetime(cells[0], format="%d %b %Y").strftime("%Y-%m-%d")
        records.append({"date": d, **dict(zip(COLUMNS, nums))})
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("date")
    return df[~df.index.duplicated(keep="last")].sort_index()


def parse_farside(content: str) -> pd.DataFrame:
    """解析 Jina 渲染的 Farside 全表（M USD）。

    新格式：Markdown 管道表（每行 | 日期 | 13 值 |）；
    旧格式（历史/测试）：每 token 一行（日期行 + 13 值行）。
    """
    df = _parse_table(content)
    if not df.empty:
        return df
    tokens = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # 表头锚定：最后一个 "Date" token 之后、首个日期行之前为列名
    # （菜单/页脚无关 token 不计）
    idx = next((i for i, t in enumerate(tokens) if _DATE_RE.match(t)), len(tokens))
    header: list[str] = []
    last_date = max((i for i, t in enumerate(tokens[:idx]) if t == "Date"), default=-1)
    if last_date >= 0:
        header = [t for t in tokens[last_date + 1 : idx] if _DATE_RE.match(t) is None]
    # 表头与 COLUMNS 数量一致但内容不同（Jina 列序变化/噪声）→ 回退并告警；
    # 数量不同的小样本/变体页仍按页面自身 header 解析
    if header and len(header) == len(COLUMNS) and header != COLUMNS:
        logger.warning("Farside 表头与预期不一致: %s…（回退默认列序）", str(header[:5]))
        header = []
    cols = header if header else COLUMNS
    rows: list[dict] = []
    cur_date, cur_vals = None, []
    for tok in tokens[idx:]:
        if _DATE_RE.match(tok):
            if cur_date is not None:
                rows.append((cur_date, cur_vals))
            cur_date, cur_vals = tok, []
        elif cur_date is not None:
            cur_vals.append(tok)
    if cur_date is not None:
        rows.append((cur_date, cur_vals))

    records = []
    for d, vals in rows:
        # 日期行后的 token 至下一日期行；取前 len(cols) 个有效值（列序对齐表头）
        nums = [_to_float(v) for v in vals[: len(cols)]]
        if len(nums) < len(cols):
            nums += [None] * (len(cols) - len(nums))
        rec = {"date": pd.to_datetime(d, format="%d %b %Y").strftime("%Y-%m-%d")}
        rec.update(dict(zip(cols, nums)))
        records.append(rec)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("date")
    return df


def fetch_flows() -> pd.DataFrame:
    """经 Jina Reader 拉取 Farside 全表并解析。"""
    content = jina_fetch(PAGE_URL)
    df = parse_farside(content)
    if df.empty:
        raise RuntimeError(f"Farside 解析为空（响应 {len(content)} 字符）")
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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
