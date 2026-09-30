"""工单 #14 preview 验收:playwright 抽 2 页(有图表交互的),验证图表渲染 + 暗色切换。

用法: uv run python scripts_dev/check_liquidity_playwright.py <base_url>
截图存 /tmp/liq_dark_*.png
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

PAGES = [
    ("/liquidity/rrp-tga.html", ["spread_chart", "cash_chart"]),
    ("/liquidity/reserves.html", ["chart1", "chart2"]),
]


async def main(base: str) -> int:
    ok = True
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        for path, charts in PAGES:
            console_errors = []
            page.on(
                "console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None,
            )
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_timeout(1200)
            # as-of 时效标签已填充 = 数据 fetch 成功
            as_of = await page.text_content("#re-as-of")
            n_canvas = await page.evaluate("document.querySelectorAll('canvas').length")
            n_err = len(console_errors)
            print(f"{path}  as-of=[{as_of}]  canvas={n_canvas}  errors={n_err}")
            ok &= (
                bool(as_of and "数据截至" in as_of)
                and n_canvas >= len(charts)
                and not console_errors
            )
            # 暗色切换:点悬浮按钮 → body.dark + 图表重绘(theme-changed)
            await page.click(".theme-float")
            await page.wait_for_timeout(600)
            dark = await page.evaluate("document.body.classList.contains('dark')")
            bg = await page.evaluate("getComputedStyle(document.body).backgroundColor")
            name = path.strip("/").replace("/", "_").removesuffix(".html")
            await page.screenshot(path=f"/tmp/liq_dark_{name}.png", full_page=False)
            print(f"  暗色切换: body.dark={dark} bg={bg}")
            ok &= dark
            await page.click(".theme-float")  # 切回,不影响下一页
        await browser.close()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
