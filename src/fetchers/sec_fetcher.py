"""
Fetch SEC 10-K / 10-Q filings (original text) from EDGAR.

数据源: data.sec.gov / www.sec.gov（免费、无需认证；
UA 须为「机构名 + 联系邮箱」格式，否则 WAF 403）
输出: data/sec/{SYMBOL}/{FORM}_{filing_date}.txt.gz
  = 主文档（10-Q/10-K 正文，iXBRL HTML）去标签后的纯文本，gzip 压缩（~150KB/份）
  （不含附件——全量申报 .txt 一份 10-Q 就 5.8MB，进 git 太重）
存储模式: 文档库（非时间序列），目标文件已存在即跳过 → 自然增量，无全量覆盖逻辑
默认回溯 2 年（10-K × 2 + 10-Q × 8），与 yfinance 三张表（financials_fetcher）深度匹配；
--years N 可加长。仅收录 10-K / 10-Q / 20-F 正本（跳过 /A 修正件；
20-F 是外国私人发行人（FPI，如 TSM）的年度报告，与 10-K 同级）。
注意: EDGAR 新架构文档直链必须用无破折号 accession（带破折号 404）。

用法:
  uv run python -m src.fetchers.sec_fetcher
  uv run python -m src.fetchers.sec_fetcher --symbols AAPL,MSFT
  uv run python -m src.fetchers.sec_fetcher --years 5
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import time
from datetime import datetime
from html import unescape
from pathlib import Path

import requests

from ..config import ROOT, STOCKS

logger = logging.getLogger(__name__)

BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"
# SEC 要求自动化工具声明身份：UA 必须是「机构名 + 联系邮箱」，否则 WAF 403
HEADERS = {"User-Agent": "MarketBase Research marketbase@example.com"}
DELAY = 0.3  # 请求间隔，SEC 限 10 req/s

# 直连会话：忽略 .env 的 SOCKS5 代理（yfinance 用），与 fed_fetcher 一致
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update(HEADERS)

OUT_DIR = ROOT / "data" / "sec"
# 10-K/10-Q 为美国本土发行人，20-F 为外国发行人（FPI，如 TSM）年度报告
FORMS = ("10-K", "10-Q", "20-F")

# 全部公司 ticker→CIK 映射（~1.4MB，SEC 官方文件，每轮拉一次）
# 注意在 www 域，data 域无此文件
CIK_MAP_URL = f"{BASE}/files/company_tickers.json"

# 去标签（fed_fetcher 同款；iXBRL 内联标签直接剥离）
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
# iXBRL 文档头部有命名空间/上下文定义块（无实际内容），剥离后再去标签
_IX_HEADER_RE = re.compile(r"<ix:header>.*?</ix:header>", re.S)


def _get(url: str) -> str | bytes:
    r = _SESSION.get(url, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.content


def _normalize_ticker(ticker: str) -> str:
    """去符号统一 ticker：BRK.B / BRK-B / brk.b → BRKB。"""
    return "".join(ch for ch in ticker.upper() if ch.isalnum())


def fetch_cik_map() -> dict[str, str]:
    """公司官方 ticker → CIK（str）。无匹配的符号（韩股/部分 ETF）不在映射里。"""
    raw = _get(CIK_MAP_URL)
    data = json.loads(raw)
    return {_normalize_ticker(v["ticker"]): str(v["cik_str"]) for v in data.values()}


def _filing_url(cik: str, accession: str, primary_doc: str) -> str:
    """主文档 HTML 直链（EDGAR 新架构要求无破折号 accession，否则 404）。"""
    url = f"{BASE}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_doc}"
    return url


def _extract_text(html: str) -> str:
    """去 ix:header / script / style / 标签 → 纯文本。"""
    body = _IX_HEADER_RE.sub("", html)
    body = _SKIP_RE.sub("", body)
    text = unescape(_TAG_RE.sub(" ", body))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def recent_filings(cik: str, years: int) -> list[tuple[str, str, str]]:
    """该 CIK 近 N 年的 (form, filing_date, txt_url) 列表，按日期升序。

    仅收录 10-K/10-Q 正本：跳过 /A 修正件；primaryDocument 非 HTML
    （老式 XBRL 入口页）跳过。
    """
    raw = _get(f"{DATA_BASE}/submissions/CIK{cik:0>10}.json")
    recent = json.loads(raw)["filings"]["recent"]
    cutoff = datetime.now().timestamp() - years * 365.25 * 86400

    items = []
    for acc, fdate, form, doc in zip(
        recent["accessionNumber"],
        recent["filingDate"],
        recent["form"],
        recent["primaryDocument"],
        strict=True,
    ):
        if form not in FORMS:  # 排除 10-K/A、10-Q/A
            continue
        if not doc.lower().endswith((".htm", ".html")):
            logger.debug("跳过非 HTML 主文档 %s %s", cik, doc)
            continue
        if datetime.strptime(fdate, "%Y-%m-%d").timestamp() < cutoff:
            continue
        items.append((form, fdate, _filing_url(cik, acc, doc)))
    return sorted(items, key=lambda x: x[1])


def _download_filing(url: str, dest: Path) -> bool:
    """下载主文档 HTML → 提取纯文本 → gzip 落盘。返回是否成功。"""
    try:
        html = _get(url).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 — 单条失败不阻断整体
        logger.warning("下载失败 %s: %s", url, e)
        return False
    text = _extract_text(html)
    if len(text) < 1000:
        logger.warning("文本过短 %s（%d 字符），跳过", url, len(text))
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", encoding="utf-8") as f:
        f.write(text)
    return True


def fetch_sec(
    symbols: list[str] | None = None, years: int = 2
) -> dict[str, tuple[int, int]]:
    """拉取指定股票的 10-K/10-Q 原文。symbols=None 时用配置全部股票。"""
    cik_map = fetch_cik_map()
    result: dict[str, tuple[int, int]] = {}
    for sc in STOCKS:
        ticker = sc.yf_ticker or sc.name
        if symbols and sc.name not in symbols and ticker not in symbols:
            continue
        cik = cik_map.get(_normalize_ticker(ticker))
        if not cik:
            logger.info("%s: EDGAR 无 CIK（非美股），跳过", sc.name)
            continue
        new = skipped = 0
        out_dir = OUT_DIR / sc.name
        for form, fdate, url in recent_filings(cik, years):
            dest = out_dir / f"{form}_{fdate}.txt.gz"
            if dest.exists():
                skipped += 1
                continue
            if _download_filing(url, dest):
                new += 1
        result[sc.name] = (new, skipped)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SEC 10-K/10-Q original text")
    parser.add_argument("--symbols", help="逗号分隔的股票列表（默认全部）")
    parser.add_argument("--years", type=int, default=2, help="回溯年数（默认 2）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    result = fetch_sec(symbols, args.years)
    total_new = total_skip = 0
    for name, (new, skipped) in result.items():
        logger.info("%s: 新增 %d，跳过 %d", name, new, skipped)
        total_new += new
        total_skip += skipped
    logger.info("合计: 新增 %d，跳过 %d", total_new, total_skip)


if __name__ == "__main__":
    main()
