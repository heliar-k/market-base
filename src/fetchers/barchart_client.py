"""Barchart core-api 客户端（直连失败自动降级无头浏览器）。

Barchart 页面数据由 JS 动态加载，前端统一调
`/proxies/core-api/v1/quotes/get`，需要两步请求（无 key 无登录）：

1. GET 页面（任意 Barchart 页面）→ 种 laravel_session / XSRF-TOKEN cookie
2. 带 token header 调 core-api（参数是 lists / symbol / fields 等）

免费、无 key、无登录；数据中心 IP（GitHub Actions）实测可用。
数据为延迟报价（lastPrice 等常带 "s" 后缀）。

2026-08 底 Barchart 全站上 AWS WAF JS challenge（HTTP 层 202+空 body，无 cookie），
直连路线失败后自动降级 playwright 无头浏览器：过 WAF challenge 后在页面内
fetch core-api（同源带全部 cookie）。每进程只过一次 challenge，后续复用同页面。

认证只有一套：GET 页面种下 cookie XSRF-TOKEN，请求带 X-XSRF-TOKEN header。
（2026-10 Barchart 从页面撤掉了 `<meta name="csrf-token">`，options/chain 现在
与 quotes/get 用同一个 token，旧的那套 meta 取法已删。）

供 cfets / 期货期限结构 / 期权链 等 fetcher 复用。
"""

import logging
import re
import urllib.parse

import requests

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CORE_API = "https://www.barchart.com/proxies/core-api/v1"
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


def core_get(params: dict, referer: str, endpoint: str = "quotes/get") -> dict:
    """调 Barchart core-api（直连失败自动降级无头浏览器）。

    Args:
        params: 查询参数（lists=forex.forwardCurves(^USDCNH)、symbol=ES^F、fields=...）
        referer: 任意 Barchart 页面 URL，用于种 cookie 与防盗链校验
        endpoint: core-api 子路径（默认 quotes/get；options/chain 等）
    Returns:
        JSON 响应（{data: [...]}）。
    Raises:
        RuntimeError: 直连未拿到 XSRF cookie 且浏览器通道也失败（站点改版时）
        requests.HTTPError: 非 2xx
    """
    try:
        return _core_get_http(params, referer, endpoint)
    except Exception as e:
        logger.warning("Barchart 直连失败（%s），降级无头浏览器通道", e)
        return _core_get_browser(params, referer, endpoint)


def _core_get_http(params: dict, referer: str, endpoint: str) -> dict:
    """原直连路线：requests 种 cookie + token header。"""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    session.get(referer, timeout=20)  # 只为种 laravel_session / XSRF-TOKEN
    token = session.cookies.get("XSRF-TOKEN")
    if not token:
        raise RuntimeError(f"Barchart 未获取到 XSRF cookie (referer={referer})")
    resp = session.get(
        f"{CORE_API}/{endpoint}",
        params=params,
        headers={
            "Accept": "application/json",
            "X-XSRF-TOKEN": urllib.parse.unquote(token),
            "Referer": referer,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# 无头浏览器单例：每进程只过一次 WAF challenge，后续调用复用同页面
_BROWSER = None
_PAGE = None


def _browser_page():
    """惰性启动 playwright，返回复用的 page。"""
    global _BROWSER, _PAGE
    if _PAGE is None:
        import atexit

        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _BROWSER = pw.chromium.launch(headless=True)
        ctx = _BROWSER.new_context(
            user_agent=UA,
            locale="en-US",
        )
        _PAGE = ctx.new_page()
        atexit.register(_BROWSER.close)
    return _PAGE


# 在 Barchart 页面内 fetch core-api：同源自动带 aws-waf-token 等全部 cookie，
# 有 XSRF-TOKEN 就一并带上（WAF 会话已认证时没有也能过）
_JS_FETCH = """
async ([apiUrl, referer]) => {
    const headers = {'Accept': 'application/json', 'Referer': referer};
    const m = document.cookie.split('; ').find(r => r.startsWith('XSRF-TOKEN='));
    if (m) headers['X-XSRF-TOKEN'] = decodeURIComponent(m.split('=')[1]);
    const resp = await fetch(apiUrl, {headers});
    return {status: resp.status, body: await resp.text()};
}
"""


def _core_get_browser(params: dict, referer: str, endpoint: str) -> dict:
    """降级路线：无头浏览器过 AWS WAF 后在页面内调 core-api。

    ponytail: 复用单 page（串行调用假设）；fetcher 均为顺序循环，够用。
    """
    import json as _json
    import time

    api_url = f"{CORE_API}/{endpoint}?{urllib.parse.urlencode(params)}"
    page = _browser_page()
    # 已开的页面可能带着过期/未过 challenge 的状态：直接重访 referer，
    # WAF challenge 会自动跑完并刷新页面（实测 ~5-8s）
    page.goto(referer, timeout=60000, wait_until="domcontentloaded")
    for _ in range(20):
        try:
            if page.evaluate("() => document.cookie.includes('aws-waf-token')"):
                break
        except Exception:
            pass
        time.sleep(1.5)
    result = page.evaluate(_JS_FETCH, [api_url, referer])
    if result["status"] != 200:
        raise RuntimeError(
            f"Barchart 浏览器通道失败: HTTP {result['status']} {result['body'][:100]}"
        )
    try:
        return _json.loads(result["body"])
    except ValueError as e:
        raise RuntimeError(f"Barchart 浏览器通道返回非 JSON: {e}") from e
