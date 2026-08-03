"""Barchart core-api 匿名访问客户端。

Barchart 页面数据由 JS 动态加载，前端统一调
`/proxies/core-api/v1/quotes/get`，需要两步匿名请求：

1. GET 页面（任意 Barchart 页面）→ 种 laravel_session / XSRF-TOKEN cookie
2. 带 X-XSRF-TOKEN header 调 core-api（参数是 lists / symbol / fields 等）

免费、无 key、无登录；数据中心 IP（GitHub Actions）实测可用。
数据为延迟报价（lastPrice 等常带 "s" 后缀）。

供 cfets / 期货期限结构 / 期权链 等 fetcher 复用。
"""

import logging
import urllib.parse

import requests

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CORE_API = "https://www.barchart.com/proxies/core-api/v1/quotes/get"


def core_get(params: dict, referer: str, timeout: int = 20) -> dict:
    """匿名调 Barchart core-api quotes/get。

    Args:
        params: 查询参数（lists=forex.forwardCurves(^USDCNH)、symbol=ES^F、fields=...）
        referer: 任意 Barchart 页面 URL，用于种 cookie 与防盗链校验
    Returns:
        JSON 响应（{data: [...]}）。
    Raises:
        RuntimeError: 未拿到 XSRF cookie（站点改版时）
        requests.HTTPError: 非 2xx
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    session.get(referer, timeout=timeout)  # 种 laravel_session / XSRF-TOKEN
    xsrf = session.cookies.get("XSRF-TOKEN")
    if not xsrf:
        raise RuntimeError(f"Barchart 未获取到 XSRF cookie (referer={referer})")
    resp = session.get(
        CORE_API,
        params=params,
        headers={
            "Accept": "application/json",
            "X-XSRF-TOKEN": urllib.parse.unquote(xsrf),
            "Referer": referer,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
