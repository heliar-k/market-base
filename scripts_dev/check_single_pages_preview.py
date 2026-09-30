"""工单 #16 preview 验收:8 页 curl(200 + title + 共享资源无 404)。

用法: uv run python scripts_dev/check_single_pages_preview.py <base_url>
"""

from __future__ import annotations

import re
import sys
import urllib.request

PAGES = [
    "/daily/",
    "/treasury/",
    "/inflation/",
    "/labor/",
    "/geo/",
    "/fed/",
    "/volatility/",
    "/volatility/vix.html",  # _redirects 200 重写(地址栏不变)
]

SHARED = [  # 共享资源(页面 <head>/顶栏引用的)必须 200
    "/css/app.css",
    "/css/special.css",
    "/css/fed.css",
    "/js/echarts-theme.js",
    "/js/rates-common.js",
    "/js/site-nav.js",
    "/vendor/echarts.min.js",
    "/favicon.svg",
]

EXPECT_TITLE = {
    "/daily/": "今日研判 — 观澜台",
    "/treasury/": "美债 — 观澜台",
    "/inflation/": "通胀 — 观澜台",
    "/labor/": "就业 — 观澜台",
    "/geo/": "地缘与政治风险 — 观澜台",
    "/fed/": "美联储 — 观澜台",
    "/volatility/": "波动率全景仪表盘 — 观澜台",
    "/volatility/vix.html": "VIX — 观澜台",
}

# 入口页 link-grid 内链(绝对路径)须可达
INNER_LINKS = [
    "/rates/",
    "/credit/",
    "/inflation/",
    "/liquidity/",
    "/volatility/",
    "/volatility/vix.html",
    "/assets/",
]


def get(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(base + path, headers={"User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def main(base: str) -> int:
    base = base.rstrip("/")
    ok = True
    for p in PAGES:
        st, body = get(base, p)
        m = re.search(r"<title>([^<]*)</title>", body)
        t = m.group(1) if m else "(无 title)"
        good = st == 200 and t == EXPECT_TITLE[p] and "astro" not in body.lower()[:200]
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {st} {p}  [{t}]")

    for r in SHARED:
        st, _ = get(base, r)
        ok &= st == 200
        print(f"{'OK ' if st == 200 else 'BAD'} {st} {r}")

    for link in INNER_LINKS:
        st, _ = get(base, link)
        ok &= st == 200
        print(f"{'OK ' if st == 200 else 'BAD'} {st} 内链 {link}")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
