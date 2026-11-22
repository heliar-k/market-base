"""Jina Reader 通用抓取（r.jina.ai 网页→Markdown，绕过 Cloudflare 反爬）。

用法：
    from src.fetchers.jina_reader import jina_fetch
    text = jina_fetch("https://www.coinglass.com/open-interest/BTC")

返回 Markdown 文本，解析层各自处理。
环境变量 JINA_API_KEY（可选）：带上 `Authorization: Bearer` 后按 key 计限额与 token；
不配就匿名（匿名请求被 r.jina.ai 按出口 IP 限，2026-11 起 Actions 上约一半返回 401）。
"""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0"
_TIMEOUT = 60
# 匿名档限流时 Jina 返回 403（而非 429），重试几次通常就拿到（实测同 URL 隔分钟 403→200）
_RETRY_STATUS = (403, 429)
_RETRY_BACKOFF = (3, 8)  # 秒；长度即最大重试次数


def jina_fetch(url: str, timeout: int = _TIMEOUT) -> str:
    """经 Jina Reader 抓取页面，返回 Markdown 文本。

    Raises:
        requests.HTTPError: 非 2xx
        RuntimeError: JSON 响应但缺 data.content（Jina 改版时）
    """
    proxies = {"https": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    api_key = os.environ.get("JINA_API_KEY", "").strip()
    for attempt in range(len(_RETRY_BACKOFF) + 1):
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            timeout=timeout,
            headers=headers,
            proxies=proxies,
        )
        status = getattr(resp, "status_code", 200)
        # shortcut: 退避只治间歇限流；401 是 r.jina.ai 对匿名请求按出口 IP 的拒绝，同 IP 重试无用
        # （Actions 上实测约一半班次如此），配 JINA_API_KEY 后走 key 限额
        if status in _RETRY_STATUS and attempt < len(_RETRY_BACKOFF):
            wait = _RETRY_BACKOFF[attempt]
            logger.warning("Jina %s（第 %d 次），%ds 后重试：%s", status, attempt + 1, wait, url)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    content = resp.text
    if resp.headers.get("content-type", "").startswith("application/json"):
        # Accept: application/json → Jina 返回 {data: {content: ...}}
        try:
            content = resp.json()["data"]["content"]
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"Jina JSON 响应缺 data.content: {e}") from e
    return content
