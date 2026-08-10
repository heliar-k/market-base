"""Fetch Treasury quarterly refunding statements & financing estimates.

数据源: home.treasury.gov 季度再融资专题页（官方、免费、无认证）
  - 最新季度文档页: /policy-issues/financing-the-government/quarterly-refunding/
    most-recent-quarterly-refunding-documents

抓取两类文档（按标题识别）:
  - "Policy Statement: {YYYY} - {N}th Quarter"  → kind=statement
    （Refunding Statement，关键词 "increase coupon issuance"=偏空 /
    "no change"=偏多，监控用）
  - "Financing Estimates: {YYYY} - {N}th Quarter" → kind=financing_estimates
    （QRA 借款缺口指引）

输出: data/treasury/refunding.csv — id, date, quarter, kind, title, url, body
存储模式: 文档库（非观测日序列），按 URL 去重增量（同 fed_fetcher）。
每次运行只抓最新季度页 → 每季度自动累积新文档。

用法:
  uv run python -m src.fetchers.refunding_fetcher
"""

from __future__ import annotations

import logging
import re
import time
from html import unescape
from pathlib import Path

import pandas as pd
import requests

from ..config import ROOT

logger = logging.getLogger(__name__)

BASE = "https://home.treasury.gov"
MOST_RECENT_URL = (
    f"{BASE}/policy-issues/financing-the-government/quarterly-refunding/"
    "most-recent-quarterly-refunding-documents"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (research script; +https://github.com/)"}
DELAY = 0.3  # 请求间隔，避免触发限流

# 直连会话：忽略 .env 中的 SOCKS5 代理（同 fed_fetcher）
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update(HEADERS)

OUT_CSV = ROOT / "data" / "treasury" / "refunding.csv"

# 按标题识别文档种类
_KIND_RE = {
    "statement": re.compile(r"^Policy Statement: \d{4}"),
    "financing_estimates": re.compile(r"^Financing Estimates: \d{4}"),
}
_QUARTER_RE = re.compile(r"(\d{4})\s*-\s*([1-4])(?:st|nd|rd|th)? Quarter", re.I)
_BODY_RE = re.compile(
    r"field--name-field-news-body[^>]*>(.*?)(?=<div[^>]*field--name-field-news-)",
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _get(url: str) -> str:
    r = _SESSION.get(url, timeout=20)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text


def _doc_kind(title: str) -> str:
    for kind, pattern in _KIND_RE.items():
        if pattern.match(title):
            return kind
    return ""


def _extract_date(html: str) -> str:
    """取新闻稿发布日期（field--name-field-news-publication-date 内的 time）。"""
    m = re.search(
        r"field--name-field-news-publication-date.*?<time[^>]*datetime=\"(\d{4}-\d{2}-\d{2})",
        html,
        re.S,
    )
    return m.group(1) if m else ""


def _extract_body(html: str) -> str:
    """提取新闻稿正文纯文本（field--name-field-news-body 容器）。"""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else ""
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    text = unescape(_TAG_RE.sub(" ", body))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _quarter_of(title: str) -> str:
    m = _QUARTER_RE.search(title)
    return f"{m.group(1)}-Q{m.group(2)}" if m else ""


def fetch_refunding() -> tuple[int, int]:
    """抓取最新季度文档页中的 Refunding Statement 与 Financing Estimates。

    返回 (新增, 跳过)；按 URL 去重增量写入 refunding.csv。
    """
    page = _get(MOST_RECENT_URL)
    # 页内每个新闻稿链接的文本含标题（"Policy Statement: 2026 - 3rd Quarter"）
    docs: dict[str, str] = {}  # url → title
    for url, title in re.findall(
        r'href="(/news/press-releases/[a-z0-9]+)"[^>]*>(.*?)</a>', page, re.S
    ):
        title = re.sub(r"<[^>]+>", " ", title).strip()
        title = re.sub(r"\s+", " ", title)
        if _doc_kind(title):
            docs[BASE + url] = title

    old = pd.DataFrame()
    if OUT_CSV.exists():
        old = pd.read_csv(OUT_CSV, dtype=str)
    seen = set(old["url"]) if not old.empty else set()

    rows, skipped = [], 0
    for url, title in docs.items():
        if url in seen:
            skipped += 1
            continue
        try:
            html = _get(url)
        except Exception as e:  # noqa: BLE001 — 单条失败不阻断整体
            logger.warning("抓取失败 %s: %s", url, e)
            continue
        body = _extract_body(html)
        if not body:
            logger.warning("正文为空 %s，跳过", url)
            continue
        rows.append(
            {
                "id": Path(url).name,
                "date": _extract_date(html),
                "quarter": _quarter_of(title),
                "kind": _doc_kind(title),
                "title": title,
                "url": url,
                "body": body,
            }
        )

    new_rows = pd.DataFrame(rows) if rows else old
    merged = pd.concat([old, new_rows], ignore_index=True)
    merged = merged.drop_duplicates(subset=["url"]).sort_values("date")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUT_CSV, index=False)
    return len(rows), skipped


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    new, skipped = fetch_refunding()
    logger.info("refunding: 新增 %d，跳过 %d（已有）", new, skipped)


if __name__ == "__main__":
    main()
