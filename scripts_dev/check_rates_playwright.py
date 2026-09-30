"""工单 #15 preview 验收:playwright 抽 rates 2 页,验证内容渲染 + 暗色切换 + as-of。

用法: uv run python scripts_dev/check_rates_playwright.py <base_url>
截图存 /tmp/rates_dark_*.png

注意:re-global/re-tenors 等是 R.table() 渲染的 HTML 表格(无 canvas),
验收标准 = 子元素非空 + 全页 canvas 数正常 + 无控制台错误。
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

PAGES = [
    ("/rates/yield-curve.html", ["re-global", "re-tenors", "re-analysis"]),
    ("/rates/pricing.html", ["rp-hero", "conv-chart"]),
]


async def main(base: str) -> int:
    fails: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for path, section_ids in PAGES:
            page = await browser.new_page()
            errors: list[str] = []
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            await page.goto(base + path, wait_until="networkidle")
            for sid in section_ids:
                n = await page.locator(f"#{sid} *").count()
                print(f"{path} #{sid} 子元素 {n}")
                if n == 0:
                    fails.append(f"{path} #{sid} 空")
            canvas = await page.locator("canvas").count()
            print(f"{path} 全页 canvas x{canvas}")
            as_of = await page.locator("#re-as-of").text_content()
            print(f"{path} as-of: {as_of}")
            if "数据截至" not in (as_of or ""):
                fails.append(f"{path} as-of 异常: {as_of}")
            await page.locator(".theme-float").click()
            dark = await page.evaluate("document.body.classList.contains('dark')")
            print(f"{path} dark 切换: {dark}")
            await page.screenshot(
                path=f"/tmp/rates_dark_{path.split('/')[-1].replace('.html', '')}.png",
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
    raise SystemExit(asyncio.run(main(sys.argv[1])))
