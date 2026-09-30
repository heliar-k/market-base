"""工单 #16 preview 验收:playwright 抽 fed(鹰鸽列表) + daily 2 页。

验证渲染 + 暗色 + as-of。

用法: uv run python scripts_dev/check_single_pages_playwright.py <base_url>
截图存 /tmp/single_dark_*.png

验收(fed:声明/演讲列表与鹰鸽追踪正常;daily:今日研判特殊结构):
- 表格/列表区查子元素非空(修正版逻辑,不查 canvas——两页均无图表容器)
- fed:#sec-stmts/.fed-group 折叠组、#sec-hawk 立场表、#sec-odds 概率条
- daily:#db-badges 定调徽章、#db-scenarios 情景卡、#db-table 跨资产表、#db-fomc 倒计时
- as-of 含「数据截至」/ 暗色切换生效 / console 零错误
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

# selectors = 内容容器(子元素非空即可);每页 (path, [(描述, css 选择器)])
PAGES = [
    (
        "/fed/",
        [
            ("cards 统计卡", "#cards .re-card"),
            ("声明列表", "#sec-stmts .fed-group"),
            ("演讲列表", "#sec-speeches .fed-group"),
            ("鹰鸽立场表", "#sec-hawk table tbody tr"),
            ("鹰鸽时间线图", "#chart-timeline canvas"),
            ("市场预期", "#sec-odds .fed-doc"),
        ],
    ),
    (
        "/daily/",
        [
            ("定调徽章", "#db-badges .re-badge"),
            ("情景卡", "#db-scenarios .re-card"),
            ("FOMC 倒计时", "#db-fomc .re-badge"),
            ("跨资产表", "#db-table tbody tr"),
            ("专题 link-grid", ".link-grid .link-card"),
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
            if not cur:
                fails.append(f"{path} 顶栏无 aria-current")
            await page.locator(".theme-float").click()
            await page.wait_for_timeout(500)
            dark = await page.evaluate("document.body.classList.contains('dark')")
            print(f"{path} dark 切换: {dark}")
            if not dark:
                fails.append(f"{path} 暗色切换失败")
            await page.screenshot(
                path=f"/tmp/single_dark_{path.strip('/').replace('/', '_')}.png",
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
