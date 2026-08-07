"""Yahoo Finance 个股新闻拉取（CLI + 库函数）。

绕过 yfinance 1.4.1 的 t.news（解析 bug + TLS 间歇失败），直连 NCP 接口：

    POST https://finance.yahoo.com/xhr/ncp?queryRef=latestNews&serviceKey=ncp_fin
    body: {"serviceConfig": {"snippetCount": N, "s": [TICKER]}}

要点（2026-08 实测）：
- 先 GET finance.yahoo.com/quote/{T} 拿 A1/A3/A1S cookie（requests 拿不到，
  必须 curl_cffi 的 TLS 指纹）
- curl_cffi + SOCKS5 代理 TLS 握手间歇失败 → 重试 4 次
- 文章字段在 content.title / content.summary / content.pubDate（yfinance
  解析的顶层 title 已过时）

不落盘 —— 新闻是瞬态数据，不进 CSV 轨道。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi import requests as cffi_requests

_ROOT = "https://finance.yahoo.com"


def _load_proxy() -> str:
    env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if env:
        return env
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines():
        if line.startswith("HTTPS_PROXY"):
            return line.split("=", 1)[1].strip()
    return ""


@dataclass
class NewsItem:
    title: str
    summary: str
    pub_date: str
    link: str


def _get(session: cffi_requests.Session, url: str) -> Any:
    """带重试的 GET（curl_cffi+SOCKS5 TLS 握手间歇失败）。"""
    for _ in range(4):
        try:
            return session.get(url, timeout=20)
        except Exception:
            time.sleep(1.5)
    raise RuntimeError(f"GET {url} 重试 4 次仍失败（TLS/代理问题）")


def fetch_news(ticker: str, count: int = 10) -> list[NewsItem]:
    """拉取某标的的 Yahoo Finance 新闻，按发布时间倒序。"""
    proxy = _load_proxy()
    session = cffi_requests.Session(impersonate="chrome")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    _get(session, f"{_ROOT}/quote/{ticker}")  # 拿 A1/A3/A1S cookie

    url = f"{_ROOT}/xhr/ncp?queryRef=latestNews&serviceKey=ncp_fin"
    payload = {"serviceConfig": {"snippetCount": count, "s": [ticker]}}
    for _ in range(4):
        try:
            resp = session.post(url, json=payload, timeout=20)
            break
        except Exception:
            time.sleep(1.5)
    else:
        raise RuntimeError(f"POST {url} 重试 4 次仍失败")

    stream = resp.json().get("data", {}).get("tickerStream", {}).get("stream", [])
    items = []
    for a in stream:
        if a.get("ad"):
            continue
        c = a.get("content", {})
        items.append(
            NewsItem(
                title=c.get("title", ""),
                summary=c.get("summary", ""),
                pub_date=c.get("pubDate", ""),
                link=c.get("canonicalUrl", {}).get("url", ""),
            )
        )
    return items


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="拉取 Yahoo Finance 个股新闻")
    parser.add_argument("ticker", help="股票代码，如 TSM")
    parser.add_argument("-n", "--count", type=int, default=10, help="条数（默认 10）")
    args = parser.parse_args()

    for item in fetch_news(args.ticker.upper(), args.count):
        print(f"[{item.pub_date}] {item.title}")
        if item.summary:
            print(f"    {item.summary}")
        if item.link:
            print(f"    {item.link}")
        print()


if __name__ == "__main__":
    main()
