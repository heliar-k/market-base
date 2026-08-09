"""Fetch SEC Form 4 insider transactions from EDGAR.

数据源: EDGAR（免费，UA 须含联系方式；限速 ≤10 req/s，请求间隔 0.3s）
- ticker→CIK: https://www.sec.gov/files/company_tickers.json（每次运行拉一次）
- 近期 filings: https://data.sec.gov/submissions/CIK{10位}.json（form 4/5 过滤）
- filing 文件清单: https://www.sec.gov/Archives/edgar/data/{cik}/{accession无横线}/index.json
- ownership XML: 清单里第一个 .xml（Form 4 的持有人权益文档）

输出: data/insider/{SYMBOL}.csv（长表，按 accession 去重，幂等增量）
列: filing_date, transaction_date, insider_name, title, code, shares,
    price, value, shares_after, accession
交易代码: P=公开市场买入 S=公开市场卖出（信号，CLI 汇总用）；
        A=授予 M=行权 F=缴税代扣 G=赠与（记录但不算信号）
CLI 结尾打印每标的近 90 天 open-market 净买入（P 金额 − S 金额，正=净买入；
窗口基准 = filing_date 申报日，即信号可知日）

用法:
    uv run python -m src.fetchers.insider_fetcher
    uv run python -m src.fetchers.insider_fetcher --symbols AAPL --days 365
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..config import ROOT, STOCKS
from ._io import upsert_rows
from .sec_fetcher import (  # 复用 EDGAR 基建（直连会话/UA/限速/CIK 映射）
    _SESSION,
    DELAY,
    _normalize_ticker,
    fetch_cik_map,
)

logger = logging.getLogger(__name__)

BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"

OUT_DIR = ROOT / "data" / "insider"

# 交易代码 → 中文含义（P/S 为 open-market 信号）
CODES = {
    "P": "买入",
    "S": "卖出",
    "A": "授予",
    "M": "行权",
    "F": "缴税代扣",
    "G": "赠与",
    "D": "出售给发行人",
    "J": "其他",
}

_NS_RE = re.compile(rb' xmlns[^=]*="[^"]*"')


def _get(url: str) -> bytes:
    r = _SESSION.get(url, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.content


def _recent_form4(cik: str, cutoff: datetime) -> list[tuple[str, str]]:
    """近 N 年 form 4/5 的 (accession, filing_date) 列表，日期升序。"""
    raw = json.loads(_get(f"{DATA_BASE}/submissions/CIK{cik:0>10}.json"))
    recent = raw["filings"]["recent"]
    items = []
    for acc, fdate, form in zip(
        recent["accessionNumber"], recent["filingDate"], recent["form"], strict=True
    ):
        if form not in ("4", "5"):
            continue
        if datetime.strptime(fdate, "%Y-%m-%d") < cutoff:
            continue
        items.append((acc, fdate))
    return sorted(items, key=lambda x: x[1])


def _ownership_xml_url(cik: str, accession: str) -> str | None:
    """filing 内第一个 .xml 文档（ownership XML）直链。"""
    nodash = accession.replace("-", "")
    index = json.loads(_get(f"{BASE}/Archives/edgar/data/{cik}/{nodash}/index.json"))
    for item in index["directory"]["item"]:
        name: str = item["name"]
        if name.endswith(".xml") and name not in ("FilingSummary.xml",):
            return f"{BASE}/Archives/edgar/data/{cik}/{nodash}/{name}"
    return None


def _txt(txn: ET.Element, path: str) -> str:
    """子元素文本（剥过命名空间，路径用裸标签）。"""
    node = txn.find(path)
    return node.text.strip() if node is not None and node.text else ""


def parse_ownership_xml(text: bytes) -> list[dict]:
    """解析 ownership XML → 交易行列表（非衍生交易）。

    剥 xmlns 后标签变裸名，ElementTree 路径无需命名空间前缀。
    """
    root = ET.fromstring(_NS_RE.sub(b"", text))
    owner = _txt(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    title = _txt(root, "reportingOwner/reportingOwnerRelationship/officerTitle")
    if not title:
        # 非高管（董事/10% 股东）无 officerTitle
        is_dir = _txt(root, "reportingOwner/reportingOwnerRelationship/isDirector")
        title = "董事" if is_dir == "1" else ""

    rows = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _txt(txn, "transactionCoding/transactionCode")
        if code not in CODES:
            continue
        shares = _float(txn, "transactionAmounts/transactionShares/value")
        price = _float(txn, "transactionAmounts/transactionPricePerShare/value")
        rows.append(
            {
                "transaction_date": _txt(txn, "transactionDate/value") or None,
                "insider_name": owner,
                "title": title,
                "code": code,
                "shares": shares,
                "price": price,
                "value": shares * price if shares and price else None,
                "shares_after": _float(
                    txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                ),
            }
        )
    return rows


def _float(txn: ET.Element, path: str) -> float | None:
    """子元素数值（文本为空/非数 → None）。"""
    text = _txt(txn, path)
    try:
        return float(text)
    except ValueError:
        return None


def _existing_accessions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    return set(df["accession"]) if "accession" in df.columns else set()


def fetch_insider(
    symbols: list[str] | None = None, days: int = 730
) -> dict[str, tuple[int, int]]:
    """拉取内部人交易（accession 去重增量）。symbols=None 用配置全部股票。"""
    cik_map = fetch_cik_map()
    cutoff = datetime.now() - timedelta(days=days)
    result: dict[str, tuple[int, int]] = {}
    for sc in STOCKS:
        ticker = sc.yf_ticker or sc.name
        if symbols and sc.name not in symbols and ticker not in symbols:
            continue
        cik = cik_map.get(_normalize_ticker(ticker))
        if not cik:
            logger.info("%s: EDGAR 无 CIK（非美股），跳过", sc.name)
            continue
        path = OUT_DIR / f"{sc.name}.csv"
        seen = _existing_accessions(path)
        new = skipped = 0
        rows: list[dict] = []
        for acc, fdate in _recent_form4(cik, cutoff):
            if acc in seen:
                skipped += 1
                continue
            url = _ownership_xml_url(cik, acc)
            if url is None:
                logger.warning("%s: %s 无 ownership XML，跳过", sc.name, acc)
                continue
            try:
                parsed = parse_ownership_xml(_get(url))
            except Exception as e:  # 单条失败不阻断整体
                logger.warning("%s: %s XML 解析失败: %s", sc.name, acc, e)
                continue
            for row in parsed:
                row.update({"filing_date": fdate, "accession": acc})
            rows.extend(parsed)
        if rows:
            df = pd.DataFrame(rows)[
                [
                    "filing_date",
                    "transaction_date",
                    "insider_name",
                    "title",
                    "code",
                    "shares",
                    "price",
                    "value",
                    "shares_after",
                    "accession",
                ]
            ]
            upsert_rows(
                path,
                df,
                subset=["accession"],
                sort_by=["filing_date", "transaction_date"],
            )
            new = len(df)
        result[sc.name] = (new, skipped)
    return result


def _summary(path: Path, days: int = 90) -> str:
    """近 N 天 open-market（P/S）净买入汇总，供 CLI 打印。"""
    if not path.exists():
        return "无数据"
    df = pd.read_csv(path)
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    cutoff = df["filing_date"].max() - pd.Timedelta(days=days)
    recent = df[df["filing_date"] >= cutoff]
    buy = recent[recent["code"] == "P"]
    sell = recent[recent["code"] == "S"]
    net = buy["value"].sum() - sell["value"].sum()
    sign = "净买入" if net > 0 else "净卖出" if net < 0 else "持平"
    return (
        f"近{days}天: 买入 {len(buy)} 笔 ${buy['value'].sum():,.0f} / "
        f"卖出 {len(sell)} 笔 ${sell['value'].sum():,.0f} / {sign} ${abs(net):,.0f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="SEC Form 4 内部人交易拉取")
    parser.add_argument("--symbols", help="逗号分隔标的（默认全部）")
    parser.add_argument("--days", type=int, default=730, help="回溯天数（默认 730）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    result = fetch_insider(symbols, args.days)
    for name, (new, skipped) in result.items():
        path = OUT_DIR / f"{name}.csv"
        logger.info("%s: 新增 %d，跳过 %d | %s", name, new, skipped, _summary(path))


if __name__ == "__main__":
    main()
