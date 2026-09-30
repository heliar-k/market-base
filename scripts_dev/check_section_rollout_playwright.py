# -*- coding: utf-8 -*-
"""layout/section-rollout 全站验收:playwright 扫 dist 全部 32 页。

用法: uv run python scripts_dev/check_section_rollout_playwright.py <base_url>
前置: cd frontend && npm run build && cd dist && python3 -m http.server 8899

每页验收:zero pageerror(未捕获异常) + 渲染非空(canvas/卡片/表格/error 卡任一)
+ 无横向溢出 + Section 结构存在(.re-sec 数量记录,SPA 除外)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DIST = Path(__file__).resolve().parent.parent / "frontend/dist"

# dist 下的全部页面(与 astro build 32 page 对齐)
PAGES = sorted(
    "/" + p.relative_to(DIST).parent.as_posix().rstrip(".") + "/"
    if p.relative_to(DIST).parent.as_posix() != "."
    else "/"
    for p in DIST.rglob("index.html")
)


async def main(base: str) -> int:
    fails: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for path in PAGES:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_timeout(800)
            res = await page.evaluate(
                """() => {
                  const sel = ['canvas', '.re-card', 'table', '.link-card', '.fed-doc',
                    '.vol-hero-card', '.oas-card', '.pm-topic-card', '.opt-regime',
                    '.score-card', '.chart-card', '.dash-card', '.dash-stat'].join(',');
                  return {
                    content: document.querySelectorAll(sel).length,
                    errors: document.querySelectorAll('.re-error').length,
                    secs: document.querySelectorAll('.re-sec').length,
                  };
                }"""
            )
            overflow = await page.evaluate(
                "document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth"
            )
            tag = "SPA" if path == "/" else f"{res['secs']}sec"
            print(
                f"{path:<42} 渲染元素 {res['content']:>3}  error卡 {res['errors']}  "
                f"section {tag:>5}  横向溢出 {overflow}px"
            )
            if errors:
                fails.append(f"{path} pageerror: {errors[:2]}")
            if res["content"] == 0 and res["errors"] == 0:
                fails.append(f"{path} 页面为空(无 canvas/卡片/表格)")
            if overflow > 0:
                fails.append(f"{path} 横向溢出 {overflow}px")
            await page.close()
        await browser.close()
    print(f"\n{len(PAGES)} 页扫描,失败 {len(fails)}")
    if fails:
        print("FAIL:", *fails, sep="\n  ")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
