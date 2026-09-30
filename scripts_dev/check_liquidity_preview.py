"""工单 #14 preview 验收:8 页 curl(200 + title + 共享资源无 404)。

用法: uv run python scripts_dev/check_liquidity_preview.py <base_url>
"""

from __future__ import annotations

import re
import sys
import urllib.request

PAGES = [
    "/liquidity/",
    "/liquidity/transmission-chain.html",
    "/liquidity/fed-balance-sheet.html",
    "/liquidity/operations.html",
    "/liquidity/rrp-tga.html",
    "/liquidity/reserves.html",
    "/liquidity/global-dollar.html",
    "/liquidity/subsurface.html",
]

SHARED = [  # 共享资源(页面 <head>/顶栏引用的)必须 200
    "/css/app.css",
    "/css/special.css",
    "/js/echarts-theme.js",
    "/js/rates-common.js",
    "/vendor/echarts.min.js",
    "/favicon.svg",
    "/js/site-nav.js",
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
    titles: dict[str, str] = {}
    for p in PAGES:
        st, body = get(base, p)
        m = re.search(r"<title>([^<]*)</title>", body)
        t = m.group(1) if m else "(无 title)"
        good = st == 200 and "观澜台" in t and "astro" not in body.lower()[:200]
        ok &= good
        titles[p] = t
        print(f"{'OK ' if good else 'BAD'} {st} {p}  [{t}]")

    for r in SHARED:
        st, _ = get(base, r)
        ok &= st == 200
        print(f"{'OK ' if st == 200 else 'BAD'} {st} {r}")

    # 入口页内链 7 张子页卡 href 在 dist 都可达
    _, idx = get(base, "/liquidity/")
    links = re.findall(r'href="([a-z-]+\.html)"', idx)
    for rel in links:
        st, _ = get(base, "/liquidity/" + rel)
        ok &= st == 200
        print(f"{'OK ' if st == 200 else 'BAD'} {st} 内链 /liquidity/{rel}")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
