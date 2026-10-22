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


async def wait_idle(page, timeout_ms: int = 6000) -> None:
    """等这一轮问答结束（发送按钮从「停止」回到「发送」）。"""
    await page.wait_for_timeout(150)
    for _ in range(timeout_ms // 100):
        if (await page.locator("[data-send]").inner_text()).strip() == "发送":
            return
        await page.wait_for_timeout(100)


TOOL_PATCH = r"""
() => {
  window.__REQS = [];
  window.__TOOL_HIT = 0;
  const sse = (chunks) =>
    new Response(chunks.join(''), {
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'x-llm-model': 'deepseek-flash' },
    });
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      window.__TOOL_HIT += 1;
      return new Response(JSON.stringify({ current: { '30Y': 4.812 }, as_of: '2026-10-21' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = JSON.parse(opts.body);
    window.__REQS.push(body);
    const toolRound = (body.messages || []).some((m) => m && m.role === 'tool');
    if (toolRound) {
      return sse([
        'data: {"choices":[{"delta":{"content":"30Y = "}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"4.812%"}}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        'data: [DONE]\n\n',
      ]);
    }
    return sse([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",' +
        '"function":{"name":"query_site_data","arguments":""}}]}}]}\n\n',
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,' +
        '"function":{"arguments":"{\\"endpoint\\":\\"rates/yield-curve\\"}"}}]}}]}\n\n',
      'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
      'data: [DONE]\n\n',
    ]);
  };
}
"""


async def check_tool_loop(browser, base: str) -> None:
    """工具往返：假 SSE 吐 tool_calls → 页面取数 → 回填 → 续流 → 正文；不落 thread。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(TOOL_PATCH)
    await page.locator(".ai-input").fill("验收工具循环")
    await page.locator("[data-send]").click()
    await wait_idle(page)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__TOOL_HIT")
    check(hit == 1, "工具执行器命中 /api/rates/yield-curve")
    check(len(reqs) == 2, "工具往返共两次请求")

    first = reqs[0] if reqs else {}
    second = reqs[-1] if reqs else {}
    check(bool(first.get("tools")), "首次请求带 tools")
    check(first.get("tool_choice") == "auto", "首次请求 tool_choice=auto")
    msgs = second.get("messages") or []
    call_msg = next((m for m in msgs if m.get("tool_calls")), None)
    tool_msg = next((m for m in msgs if m.get("role") == "tool"), None)
    check(bool(call_msg), "回填请求里有 assistant.tool_calls")
    check(
        bool(call_msg)
        and call_msg["tool_calls"][0]["function"]["arguments"]
        == json.dumps({"endpoint": "rates/yield-curve"}, separators=(",", ":")),
        "分片拼回的 arguments 完整",
    )
    check(bool(tool_msg) and tool_msg.get("tool_call_id") == "call_1", "tool 消息带 tool_call_id")
    check("4.812" in (tool_msg or {}).get("content", ""), "tool 结果回填的是端点读数")
    check(bool(second.get("tools")), "续流请求仍带 tools")

    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("4.812" in body, "最终正文渲染出工具结果")
    check("查数 rates/yield-curve" in body, "meta 回显查过的端点")

    thread = await page.evaluate("() => localStorage.getItem('guanlantai-ai-threads') || ''")
    check('"role":"tool"' not in thread, "thread 里没有 tool 角色")
    check("tool_calls" not in thread, "thread 里没有 tool_calls")
    await ctx.close()


FORCE_PATCH = r"""
() => {
  window.__REQS = [];
  const sse = (chunks) =>
    new Response(chunks.join(''), {
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'x-llm-model': 'deepseek-flash' },
    });
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      return new Response(JSON.stringify({ current: { '30Y': 4.812 } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = JSON.parse(opts.body);
    window.__REQS.push(body);
    // 模型「钻牛角尖」：只要还允许调工具就一直要工具，只有被禁了才开口
    if (body.tool_choice === 'none') {
      return sse([
        'data: {"choices":[{"delta":{"content":"收到上限，已尽力作答：30Y = 4.812%"}}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        'data: [DONE]\n\n',
      ]);
    }
    return sse([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",' +
        '"function":{"name":"query_site_data","arguments":"{\\"endpoint\\":\\"rates/yield-curve\\"}"}}]}}]}\n\n',
      'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
      'data: [DONE]\n\n',
    ]);
  };
}
"""


async def check_tool_force_answer(browser, base: str) -> None:
    """预算用尽仍在要工具 → 最后一轮强制 tool_choice:none，必须落到正文而不是空白。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(FORCE_PATCH)
    await page.locator(".ai-input").fill("验收强制作答")
    await page.locator("[data-send]").click()
    await wait_idle(page)

    reqs = await page.evaluate("() => window.__REQS")
    # 2 轮往返（各执行一次工具）+ 第 3 次仍要工具 → 第 4 次强制开口
    check(len(reqs) == 4, "钻牛角尖时四次请求（1 首问 + 2 查数 + 1 强制）")
    check(
        bool(reqs) and [r.get("tool_choice") for r in reqs[:3]] == ["auto"] * 3,
        "前三次 tool_choice 仍是 auto",
    )
    check(
        len(reqs) >= 4 and reqs[3].get("tool_choice") == "none",
        "预算用尽后最后一轮 tool_choice=none",
    )
    check(len(reqs) >= 4 and bool(reqs[3].get("tools")), "强制作答轮仍带 tools")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("4.812" in body, "强制作答轮渲染出正文（不是「接口没有返回内容」）")
    await ctx.close()


MISSING_PATCH = r"""
() => {
  window.__REQS = [];
  const sse = (chunks) =>
    new Response(chunks.join(''), {
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'x-llm-model': 'deepseek-flash' },
    });
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      return new Response('{"error":"not exported"}', {
        status: 404,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = JSON.parse(opts.body);
    window.__REQS.push(body);
    if ((body.messages || []).some((m) => m && m.role === 'tool')) {
      return sse([
        'data: {"choices":[{"delta":{"content":"本期未导出。"}}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        'data: [DONE]\n\n',
      ]);
    }
    return sse([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_404","type":"function",' +
        '"function":{"name":"query_site_data","arguments":"{\\"endpoint\\":\\"rates/yield-curve\\"}"}}]}}]}\n\n',
      'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
      'data: [DONE]\n\n',
    ]);
  };
}
"""


async def check_tool_404(browser, base: str) -> None:
    """端点 404（静态站可合法缺数据）→ 结构化结果回填，对话不断、正文照出。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(MISSING_PATCH)
    await page.locator(".ai-input").fill("验收 404")
    await page.locator("[data-send]").click()
    await wait_idle(page)

    reqs = await page.evaluate("() => window.__REQS")
    tool_msg = None
    for r in reqs:
        for m in r.get("messages") or []:
            if m.get("role") == "tool":
                tool_msg = m
    check(bool(tool_msg), "404 后仍把 tool 结果回填（对话没断）")
    payload = json.loads((tool_msg or {}).get("content") or "{}")
    check(payload.get("status") == 404, "回填结果是结构化错误：status=404")
    check("未导出" in str(payload.get("error") or ""), "错误文案说明本期未导出")
    check(len(reqs) == 2, "404 后照常发起续流请求")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("本期未导出" in body, "正文照常渲染")
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
        await check_tool_loop(browser, base)
        await check_tool_force_answer(browser, base)
        await check_tool_404(browser, base)
        await check_save_note(browser, base)
        await browser.close()
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    raise SystemExit(asyncio.run(main(target)))
