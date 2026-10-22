"""伴读抽屉 Playwright 验收：代理 / 探测 / 缓存 / effort / 存笔记。

用法（需要先起一个能打开 /rates/ 的站点）：
    uv run python scripts_dev/check_ai_chat_playwright.py http://127.0.0.1:8000
    uv run python scripts_dev/check_ai_chat_playwright.py http://127.0.0.1:4399  # 静态 dist

脚本用 page.route 假造 /api/* 与直连上游，不花钱、不连网。覆盖两条 P1 验收：
- 探测模型/换模型后，未保存的 Base URL + API Key 仍在（readCfgDraft + renderCfg 草稿）；
- 模型表按 base + 代理/直连模式缓存，代理白名单不会串进 BYOK；
- effort 在缓存有效时发送，缓存过期时页面加载即清空且不发送。
"""

from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright

SSE = 'data: {"choices":[{"delta":{"content":"验收答"}}]}\n\ndata: [DONE]\n\n'
# 思考模式必须用「慢流」验：route.fulfill 一次性整包会让「思考中」那一瞬直接跳过去。
# 在页面里替换 fetch，逐块吐 SSE，才能看到展开态与转圈图标。
THINK_PATCH = """
() => {
  const chunks = [
    'data: {"choices":[{"delta":{"reasoning_content":"先看曲线形态，"}}]}\\n\\n',
    'data: {"choices":[{"delta":{"reasoning_content":"再看期限结构。"}}]}\\n\\n',
    'data: {"choices":[{"delta":{"content":"验收答"}}]}\\n\\n',
    'data: [DONE]\\n\\n'
  ];
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    if (String(url).indexOf('/api/chat') < 0) return orig(url, opts);
    const enc = new TextEncoder();
    let i = 0;
    const stream = new ReadableStream({
      start(c) {
        const t = setInterval(() => {
          if (i >= chunks.length) { clearInterval(t); c.close(); return; }
          c.enqueue(enc.encode(chunks[i++]));
        }, 400);
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'x-llm-model': 'deepseek-flash' },
    });
  };
}
"""
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


async def new_page(browser, base: str):
    ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await ctx.new_page()
    await page.goto(base + "rates/", wait_until="domcontentloaded")
    await page.evaluate("localStorage.clear()")
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(900)
    await page.locator(".ai-float").click()
    await page.wait_for_timeout(250)
    return ctx, page


async def check_proxy_model_header(browser, base: str) -> None:
    ctx, page = await new_page(browser, base)
    seen: list[dict] = []

    async def chat(route):
        seen.append(
            {
                "url": route.request.url,
                "auth": route.request.headers.get("authorization"),
                "body": json.loads(route.request.post_data),
            }
        )
        await route.fulfill(
            status=200,
            headers={"content-type": "text/event-stream", "x-llm-model": "deepseek-flash"},
            body=SSE,
        )

    await page.route("**/api/chat", chat)
    await page.locator(".ai-input").fill("代理验收")
    await page.locator("[data-send]").click()
    await page.wait_for_timeout(900)
    meta = await page.locator(".ai-msg-assistant .ai-msg-meta").last.inner_text()
    check(bool(seen) and seen[0]["url"].endswith("/api/chat"), "默认配置走同源 /api/chat")
    check(seen[0]["auth"] is None, "代理模式不向浏览器暴露服务端 Key")
    check("模型 deepseek-flash" in meta, "答案 meta 显示实际使用的模型")
    await ctx.close()


async def check_probe_keeps_fields(browser, base: str) -> None:
    ctx, page = await new_page(browser, base)

    async def models(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "data": [
                        {"id": "m1", "display_name": "模型一", "reasoning_levels": ["low", "high"]}
                    ]
                }
            ),
        )

    await page.route("**/v1/models", models)
    await page.locator('[data-act="cfg"]').first.click()
    await page.fill('[data-k="baseUrl"]', "https://example.com/v1")
    await page.fill('[data-k="apiKey"]', "sk-keepme")
    await page.locator("[data-probe]").click()
    await page.wait_for_timeout(700)
    check(
        await page.locator('[data-k="baseUrl"]').input_value() == "https://example.com/v1",
        "探测后未保存的 Base URL 仍在",
    )
    check(
        await page.locator('[data-k="apiKey"]').input_value() == "sk-keepme",
        "探测后未保存的 API Key 仍在",
    )
    await page.select_option('[data-k="model"]', "m1")
    await page.wait_for_timeout(250)
    check(
        await page.locator('[data-k="baseUrl"]').input_value() == "https://example.com/v1",
        "换模型后 Base URL 仍在",
    )
    check(
        await page.locator('[data-k="apiKey"]').input_value() == "sk-keepme",
        "换模型后 API Key 仍在",
    )
    await ctx.close()


async def check_cache_mode(browser, base: str) -> None:
    ctx, page = await new_page(browser, base)

    async def proxy_models(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "data": [{"id": "deepseek-flash", "display_name": "deepseek-flash"}],
                    "proxy": True,
                }
            ),
        )

    await page.route("**/api/models", proxy_models)
    await page.locator('[data-act="cfg"]').first.click()
    await page.locator("[data-probe]").click()
    await page.wait_for_timeout(600)
    cache = json.loads(await page.evaluate("localStorage.getItem('guanlantai-ai-model-cache')"))
    check(cache.get("proxy") is True, "代理探测的模型缓存标了 proxy 模式")
    await page.fill('[data-k="apiKey"]', "sk-openai")
    await page.locator("[data-cfgsave]").click()
    await page.wait_for_timeout(300)
    await page.locator('[data-act="cfg"]').first.click()
    await page.wait_for_timeout(250)
    options = await page.locator('[data-k="model"] option').all_inner_texts()
    check(
        all("deepseek" not in o for o in options),
        "填 BYOK Key 后不再显示代理白名单模型（缓存按模式隔离）",
    )
    await ctx.close()


async def check_effort(browser, base: str) -> None:
    ctx, page = await new_page(browser, base)
    bodies: list[dict] = []

    async def direct(route):
        bodies.append(json.loads(route.request.post_data))
        await route.fulfill(status=200, headers={"content-type": "text/event-stream"}, body=SSE)

    await page.route("https://api.example.com/v1/chat/completions", direct)
    await page.evaluate(
        """() => {
          const now = Date.now();
          localStorage.setItem('guanlantai-ai-config', JSON.stringify({
            baseUrl: 'https://api.example.com/v1', apiKey: 'sk-x', model: 'm1', effort: 'high'
          }));
          localStorage.setItem('guanlantai-ai-model-cache', JSON.stringify({
            base: 'https://api.example.com/v1', proxy: false, ts: now,
            list: [{ id: 'm1', reasoning_levels: ['low', 'high'] }]
          }));
        }"""
    )
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(900)
    await page.locator(".ai-float").click()
    await page.locator(".ai-input").fill("有效缓存")
    await page.locator("[data-send]").click()
    await page.wait_for_timeout(900)
    check(
        bool(bodies) and bodies[0].get("reasoning_effort") == "high", "缓存有效时发送 effort=high"
    )

    await page.evaluate(
        """() => {
          localStorage.setItem('guanlantai-ai-config', JSON.stringify({
            baseUrl: 'https://api.example.com/v1', apiKey: 'sk-x', model: 'm1', effort: 'high'
          }));
          localStorage.setItem('guanlantai-ai-model-cache', JSON.stringify({
            base: 'https://api.example.com/v1', proxy: false, ts: 1, list: []
          }));
        }"""
    )
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(900)
    saved = json.loads(await page.evaluate("localStorage.getItem('guanlantai-ai-config')"))
    check(saved["effort"] == "", "缓存过期后页面加载即清空已存 effort（无需打开设置）")
    await page.locator(".ai-float").click()
    await page.locator(".ai-input").fill("过期缓存")
    await page.locator("[data-send]").click()
    await page.wait_for_timeout(900)
    check(
        bool(bodies) and "reasoning_effort" not in bodies[-1],
        "缓存过期后请求不带 effort",
    )
    await ctx.close()


async def check_save_note(browser, base: str) -> None:
    ctx, page = await new_page(browser, base)
    sent: list[dict] = []

    async def notes(route):
        sent.append(json.loads(route.request.post_data))
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "path": "data/notes/demo.md"}),
        )

    await page.route("**/api/notes", notes)
    await page.evaluate(
        """() => {
          const now = Date.now();
          localStorage.setItem('guanlantai-ai-threads', JSON.stringify([{
            id: 't1', title: '验收存笔记', createdAt: now, updatedAt: now,
            messages: [{ role: 'user', content: 'hi', ts: now },
                       { role: 'assistant', content: '你好', model: 'deepseek-flash', ts: now }]
          }]));
          localStorage.setItem('guanlantai-ai-active-thread', 't1');
        }"""
    )
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(900)
    await page.locator(".ai-float").click()
    await page.locator('[data-act="save"]').first.click()
    await page.wait_for_timeout(400)
    flash = await page.locator("[data-flash]").inner_text()
    check(("已保存" in flash) and ("demo.md" in flash), "存笔记成功并在顶部细条回显路径")
    check(bool(sent) and "模型 deepseek-flash" in sent[0]["markdown"], "导出的 Markdown 带模型口径")
    await ctx.close()


async def check_thinking_ui(browser, base: str) -> None:
    """思考模式：流式时展开 + 转圈 + 逐字显示；正文一到收成一行，点开可回看。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(THINK_PATCH)
    await page.locator(".ai-input").fill("验收思考")
    await page.locator("[data-send]").click()
    await page.wait_for_timeout(1000)  # 两个思维链分片已到，正文还没来

    think = page.locator(".ai-msg-assistant .ai-think").last
    check(await think.count() > 0, "思考栏出现")
    check(await think.evaluate("el => el.open") is True, "思考时自动展开")
    check(await think.locator(".ai-spin").count() == 1, "思考时有转圈图标")
    check(
        "先看曲线形态" in await think.locator(".ai-think-body").evaluate("el => el.textContent"),
        "思维链逐字渲染",
    )

    await page.wait_for_timeout(2200)  # 等 [DONE] 与收尾重渲染
    think = page.locator(".ai-msg-assistant .ai-think").last
    check(await think.evaluate("el => el.open") is False, "答完自动折叠")
    check((await think.locator("summary").inner_text()).startswith("思考 "), "折叠后标题是耗时")
    await think.locator("summary").click()
    await page.wait_for_timeout(150)
    check(await think.evaluate("el => el.open") is True, "点标题可重新展开")
    await ctx.close()


async def main(base: str) -> int:
    base = base.rstrip("/") + "/"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await check_proxy_model_header(browser, base)
        await check_probe_keeps_fields(browser, base)
        await check_cache_mode(browser, base)
        await check_effort(browser, base)
        await check_thinking_ui(browser, base)
        await check_save_note(browser, base)
        await browser.close()
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    raise SystemExit(asyncio.run(main(target)))
