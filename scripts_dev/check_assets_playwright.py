"""工单 #17 preview 验收:playwright 抽 equities/options(全站最大页) + crypto 2 页。

验证渲染 + 暗色 + as-of + 控制台零错误。

用法: uv run python scripts_dev/check_assets_playwright.py <base_url>
截图存 /tmp/assets_dark_*.png

验收(两页均含 ECharts 图表,查 canvas 数正常):
- options:Regime 大卡 + 22 标的看板(3 分组表) + Tab 切换后 GEX canvas 渲染
- crypto:前瞻雷达信号行 + 流动性 4 KPI 卡 + 净流动性 vs BTC 图 + 近期收盘表
- as-of 含「数据截至」/ 暗色切换生效 / aria-current=大类资产 / console 零错误
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

# selectors = 内容容器(子元素非空) / canvas 数(图表页);每页 (path, [(描述, css 选择器)])
PAGES = [
    (
        "/assets/equities/options.html",
        [
            ("Regime 大卡", "#detail .opt-regime"),
            ("4 指标卡", "#detail .re-card"),
            ("看板分组表", "#board .re-section tbody tr"),
            ("Tab 按钮", "#opt-tabs button"),
            ("默认 GEX 图 canvas", "#opt-panel .chart canvas"),
        ],
    ),
    (
        "/assets/crypto.html",
        [
            ("价格卡", "#cards .re-card"),
            ("前瞻雷达卡", "#radar .re-card"),
            ("雷达信号行", "#radar .re-render div"),
            ("流动性 KPI 卡", "#liquidity .re-card"),
            ("净流动性图 canvas", "#liq-chart canvas"),
            ("近期收盘表", "#recent tbody tr"),
        ],
    ),
]


async def main(base: str) -> int:
    fails: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for path, sels in PAGES:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_timeout(1000)
            for desc, sel in sels:
                n = await page.locator(sel).count()
                print(f"{path} {desc}({sel}) x{n}")
                if n == 0:
                    fails.append(f"{path} {desc} 为空")
            as_of = await page.locator("#re-as-of").text_content()
            print(f"{path} as-of: {as_of}")
            if "数据截至" not in (as_of or ""):
                fails.append(f"{path} as-of 异常: {as_of}")
            cur = await page.locator(".re-nav a[aria-current='page']").text_content()
            print(f"{path} aria-current: {cur}")
            if cur != "大类资产":
                fails.append(f"{path} aria-current 异常: {cur}")
            await page.locator(".theme-float").click()
            await page.wait_for_timeout(500)
            dark = await page.evaluate("document.body.classList.contains('dark')")
            print(f"{path} dark 切换: {dark}")
            if not dark:
                fails.append(f"{path} 暗色切换失败")
            await page.screenshot(
                path=f"/tmp/assets_dark_{path.strip('/').replace('/', '_')}.png",
                full_page=True,
            )
            if errors:
                fails.append(f"{path} 控制台错误: {errors[:3]}")
            await page.close()
        await browser.close()
    if fails:
        print("FAIL:", *fails, sep="\n  ")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
