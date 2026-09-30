"""工单 #15 preview 验收:playwright 抽 rates 2 页,验证渲染 + 暗色切换 + as-of。

用法: uv run python scripts_dev/check_rates_playwright.py <base_url>
截图存 /tmp/rates_dark_*.png

验收:图表容器内 canvas ≥1(re-chart 挂载成功) / 内容区块子元素非空 /
as-of 含「数据截至」/ 暗色切换生效 / console 零错误。
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

# chart_ids = ECharts 挂载容器(必须有 canvas);section_ids = 表格/文本容器(非空即可)
PAGES = [
    (
        "/rates/yield-curve.html",
        ["yc_compare", "yc_change", "yc_spreads", "yc_tips"],
        ["re-global", "re-tenors", "re-analysis"],
    ),
    (
        "/rates/pricing.html",
        ["conv-chart", "triple_chart", "spread_2_10_chart"],
        ["rp-hero", "re-tbody"],
    ),
]


async def main(base: str) -> int:
    fails: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for path, chart_ids, section_ids in PAGES:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_timeout(1000)
            for cid in chart_ids:
                n = await page.locator(f"#{cid} canvas").count()
                print(f"{path} #{cid} canvas x{n}")
                if n == 0:
                    fails.append(f"{path} #{cid} 无 canvas")
            for sid in section_ids:
                n = await page.locator(f"#{sid} *").count()
                print(f"{path} #{sid} 子元素 {n}")
                if n == 0:
                    fails.append(f"{path} #{sid} 空")
            as_of = await page.locator("#re-as-of").text_content()
            print(f"{path} as-of: {as_of}")
            if "数据截至" not in (as_of or ""):
                fails.append(f"{path} as-of 异常: {as_of}")
            await page.locator(".theme-float").click()
            await page.wait_for_timeout(500)
            dark = await page.evaluate("document.body.classList.contains('dark')")
            print(f"{path} dark 切换: {dark}")
            if not dark:
                fails.append(f"{path} 暗色切换失败")
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
