"""Barchart core-api 匿名访问客户端。

Barchart 页面数据由 JS 动态加载，前端统一调
`/proxies/core-api/v1/quotes/get`，需要两步匿名请求：

1. GET 页面（任意 Barchart 页面）→ 种 laravel_session / XSRF-TOKEN cookie
2. 带 token header 调 core-api（参数是 lists / symbol / fields 等）

免费、无 key、无登录；数据中心 IP（GitHub Actions）实测可用。
数据为延迟报价（lastPrice 等常带 "s" 后缀）。

两种认证：
- auth="xsrf"（默认）: cookie XSRF-TOKEN → X-XSRF-TOKEN header（quotes/get）
- auth="csrf": 页面 <meta name=csrf-token> → X-CSRF-TOKEN header（options/chain）

供 cfets / 期货期限结构 / 期权链 等 fetcher 复用。
"""

import logging
import re
import urllib.parse
from typing import Literal

import requests

logger = logging.getLogger(__name__)

# core-api 两种认证方式：cookie XSRF-TOKEN（quotes/get）或页面 meta csrf-token
# （options/chain）
AuthMode = Literal["xsrf", "csrf"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CORE_API = "https://www.barchart.com/proxies/core-api/v1"
META_CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
_NUM_RE = re.compile(r"-?[\d,]+\.?\d*")
_PCT_RE = re.compile(r"-?[\d.]+")


def to_float(v: str | None) -> float | None:
    """清洗 Barchart 数值：'5,966'→5966.0；'28.39%'→0.2839；'9,108.25s'→9108.25。"""
    if v is None or v == "N/A":
        return None
    s = str(v).strip()
    if s.endswith("%"):
        m = _PCT_RE.search(s)
        return float(m.group()) / 100 if m else None
    m = _NUM_RE.search(s)
    return float(m.group().replace(",", "")) if m else None


def core_get(
    params: dict,
    referer: str,
    auth: AuthMode = "xsrf",
    endpoint: str = "quotes/get",
    timeout: int = 20,
) -> dict:
    """匿名调 Barchart core-api。

    Args:
        params: 查询参数（lists=forex.forwardCurves(^USDCNH)、symbol=ES^F、fields=...）
        referer: 任意 Barchart 页面 URL，用于种 cookie 与防盗链校验
        auth: "xsrf"（cookie XSRF-TOKEN）或 "csrf"（页面 meta csrf-token）
        endpoint: core-api 子路径（默认 quotes/get；options/chain 等）
    Returns:
        JSON 响应（{data: [...]}）。
    Raises:
        RuntimeError: 未拿到对应 token（站点改版时）
        requests.HTTPError: 非 2xx
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    html = session.get(referer, timeout=timeout).text
    if auth == "csrf":
        m = META_CSRF_RE.search(html)
        if not m:
            raise RuntimeError(
                f"Barchart 页面未找到 csrf-token meta (referer={referer})"
            )
        token, header = m.group(1), "X-CSRF-TOKEN"
    else:
        token = session.cookies.get("XSRF-TOKEN")
        if not token:
            raise RuntimeError(f"Barchart 未获取到 XSRF cookie (referer={referer})")
        token, header = urllib.parse.unquote(token), "X-XSRF-TOKEN"
    resp = session.get(
        f"{CORE_API}/{endpoint}",
        params=params,
        headers={
            "Accept": "application/json",
            header: token,
            "Referer": referer,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
