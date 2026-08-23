"""Jina Reader 通用抓取（r.jina.ai 网页→Markdown，绕过 Cloudflare 反爬）。

用法：
    from src.fetchers.jina_reader import jina_fetch
    text = jina_fetch("https://farside.co.uk/bitcoin-etf-flow-all-data/")

返回 Markdown 文本（Jina 把 HTML 表渲染成"每 token 一行"，解析层各自处理）。
免费额度 ~20 RPM；每日 cron 一次足够，勿用于高频轮询。
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0"
_TIMEOUT = 60


def jina_fetch(url: str, timeout: int = _TIMEOUT) -> str:
    """经 Jina Reader 抓取页面，返回 Markdown 文本。

    Raises:
        requests.HTTPError: 非 2xx
        RuntimeError: JSON 响应但缺 data.content（Jina 改版时）
    """
    proxies = (
        {"https": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    )
    resp = requests.get(
        f"https://r.jina.ai/{url}",
        timeout=timeout,
        headers={"User-Agent": _UA, "Accept": "application/json"},
        proxies=proxies,
    )
    resp.raise_for_status()
    content = resp.text
    if resp.headers.get("content-type", "").startswith("application/json"):
        # Accept: application/json → Jina 返回 {data: {content: ...}}
        try:
            content = resp.json()["data"]["content"]
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"Jina JSON 响应缺 data.content: {e}") from e
    return content
