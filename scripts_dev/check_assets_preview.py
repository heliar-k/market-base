"""工单 #17 preview 验收:assets 族 11 页 curl(200 + title + 共享资源无 404)。

10 个非 index 子页全部走 _redirects 200 重写(地址栏不变),含嵌套
/assets/equities/{options,positioning}.html 与 SITE_NAV 子页路径形态对照。

用法: uv run python scripts_dev/check_assets_preview.py <base_url>
"""

from __future__ import annotations

import re
import sys
import urllib.request

PAGES = [
    "/assets/",
    "/assets/equities.html",
    "/assets/etfs.html",
    "/assets/bonds.html",
    "/assets/commodities.html",
    "/assets/fx.html",
    "/assets/crypto.html",
    "/assets/crypto-derivatives.html",
    "/assets/crypto-options.html",
    "/assets/equities/options.html",  # 全站最大页(414 行)
    "/assets/equities/positioning.html",
]

SHARED = [  # 共享资源(页面 <head>/顶栏引用的)必须 200
    "/css/app.css",
    "/css/special.css",
    "/js/echarts-theme.js",
    "/js/rates-common.js",
    "/js/site-nav.js",
    "/vendor/echarts.min.js",
    "/favicon.svg",
]

EXPECT_TITLE = {
    "/assets/": "大类资产 — 观澜台",
    "/assets/equities.html": "美股 — 观澜台",
    "/assets/etfs.html": "ETF — 观澜台",
    "/assets/bonds.html": "债券 — 观澜台",
    "/assets/commodities.html": "大宗商品 — 观澜台",
    "/assets/fx.html": "外汇 — 观澜台",
    "/assets/crypto.html": "加密货币 — 观澜台",
    "/assets/crypto-derivatives.html": "加密衍生品 · 资金与杠杆 — 观澜台",
    "/assets/crypto-options.html": "加密期权 · 预测市场 — 观澜台",
    "/assets/equities/options.html": "期权市场结构 — 观澜台",
    "/assets/equities/positioning.html": "持仓追踪（CFTC COT） — 观澜台",
}


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

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
