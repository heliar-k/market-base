"""伴读抽屉 Playwright 验收：代理 / 探测 / 缓存 / effort / 存笔记 / 工具循环。

用法（需要先起一个能打开 /rates/ 的站点）：
    uv run python scripts_dev/check_ai_chat_playwright.py http://127.0.0.1:8000
    uv run python scripts_dev/check_ai_chat_playwright.py http://127.0.0.1:4399  # 静态 dist

脚本用 page.route 假造 /api/* 与直连上游，不花钱、不连网。覆盖 P1 验收：
- 探测模型/换模型后，未保存的 Base URL + API Key 仍在（readCfgDraft + renderCfg 草稿）；
- 模型表按 base + 代理/直连模式缓存，代理白名单不会串进 BYOK；
- effort 在缓存有效时发送，缓存过期时页面加载即清空且不发送；
- 工具循环正常往返 / 预算用尽强制作答 / 端点 404；
- T3 失败与降级：单次超时降级、同轮重复调用只取一次数、全部端点失败时 meta 有失败标记；
- T4 跨端点工具：相关性结果带标的名字（不是裸矩阵）、期权工具查未知标的返回结构化错误
  （含可用清单）且对话不中断。
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


# ── T3：失败与降级（慢端点 / 同轮重复调用 / 全部端点失败） ─────────────────
# 三个假 fetch 共用一段 SSE 构建 helper：tool_calls 的 arguments 本身是 JSON 字符串，
# 用 JSON.stringify 现场拼，避免在 Python 字符串里叠四层转义。
_JS_TOOL_PRELUDE = r"""
  const sse = (chunks) =>
    new Response(chunks.join(''), {
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'x-llm-model': 'deepseek-flash' },
    });
  const ev = (obj) => 'data: ' + JSON.stringify(obj) + '\n\n';
  const DONE = 'data: [DONE]\n\n';
  const toolCallNamed = (name, index, id, args) =>
    ev({
      choices: [
        {
          delta: {
            tool_calls: [
              {
                index: index,
                id: id,
                type: 'function',
                function: { name: name, arguments: args },
              },
            ],
          },
        },
      ],
    });
  const toolCall = (index, id, args) => toolCallNamed('query_site_data', index, id, args);
  const text = (t) => ev({ choices: [{ delta: { content: t } }] });
  const stop = ev({ choices: [{ delta: {}, finish_reason: 'stop' }] });
  const needTools = ev({ choices: [{ delta: {}, finish_reason: 'tool_calls' }] });
  const json = (value, status) =>
    new Response(JSON.stringify(value), {
      status: status || 200,
      headers: { 'content-type': 'application/json' },
    });
  const chatBody = (opts) => JSON.parse(opts.body);
  const hasToolMsg = (body) => (body.messages || []).some((m) => m && m.role === 'tool');
"""

SLOW_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    "  window.__SLOW_HIT = 0;\n"
    + _JS_TOOL_PRELUDE
    + r"""
  // 延迟 4.5s：超过单次 3.5s 上限，但没超过 8s 批次预算。
  // 必须像真 fetch 一样响应 abort，否则「单次超时靠 signal 中断」这条路径验不到。
  const sleep = (ms, signal) =>
    new Promise((resolve, reject) => {
      const abort = () => {
        const e = new Error('The operation was aborted.');
        e.name = 'AbortError';
        reject(e);
      };
      if (signal) {
        if (signal.aborted) return abort();
        if (signal.addEventListener) signal.addEventListener('abort', abort, { once: true });
      }
      setTimeout(() => {
        if (signal && signal.removeEventListener) signal.removeEventListener('abort', abort);
        resolve();
      }, ms);
    });
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      window.__SLOW_HIT += 1;
      await sleep(4500, opts && opts.signal);
      return json({ current: { '30Y': 4.812 }, as_of: '2026-10-21' });
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('慢端点超时，先按页面摘要回答：10Y 约 4.2%。'), stop, DONE]);
    }
    return sse([
      toolCall(0, 'call_slow', JSON.stringify({ endpoint: 'rates/yield-curve' })),
      needTools,
      DONE,
    ]);
  };
}
"""
)

DUP_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    "  window.__TOOL_HIT = 0;\n"
    + _JS_TOOL_PRELUDE
    + r"""
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      window.__TOOL_HIT += 1;
      return json({
        yield_curve: { spreads: { '2s10s': 33, '3m10s': -12 } },
        as_of: '2026-10-21',
      });
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('同轮只取一次数：2s10s = 33bp。'), stop, DONE]);
    }
    // 两个不同 id 的调用，端点与 json_path 完全相同 → 同轮缓存应只发一次 GET
    const args = JSON.stringify({
      endpoint: 'rates/yield-curve',
      json_path: 'yield_curve.spreads',
    });
    return sse([
      toolCall(0, 'call_dup_a', args),
      toolCall(1, 'call_dup_b', args),
      needTools,
      DONE,
    ]);
  };
}
"""
)

FAIL_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    + _JS_TOOL_PRELUDE
    + r"""
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) return json({ error: 'not exported' }, 404);
    if (u.indexOf('/api/credit/stress') >= 0) return json({ error: 'not exported' }, 404);
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('两个端点本期未导出，无法给出读数。'), stop, DONE]);
    }
    const argsA = JSON.stringify({
      endpoint: 'rates/yield-curve',
      json_path: 'yield_curve.spreads',
    });
    const argsB = JSON.stringify({ endpoint: 'credit/stress', json_path: 'composite' });
    return sse([
      toolCall(0, 'call_fail_a', argsA),
      toolCall(1, 'call_fail_b', argsB),
      needTools,
      DONE,
    ]);
  };
}
"""
)

# 控制组：同样两个端点的成功形状，用来对照「失败 meta 不能跟成功长得一样」。
OK_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    + _JS_TOOL_PRELUDE
    + r"""
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/rates/yield-curve') >= 0) {
      return json({ yield_curve: { spreads: { '2s10s': 33, '3m10s': -12 } } });
    }
    if (u.indexOf('/api/credit/stress') >= 0) return json({ composite: 44.7, zone: '中性' });
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('两个端点都查到了：2s10s = 33bp，压力 44.7。'), stop, DONE]);
    }
    const argsA = JSON.stringify({
      endpoint: 'rates/yield-curve',
      json_path: 'yield_curve.spreads',
    });
    const argsB = JSON.stringify({ endpoint: 'credit/stress', json_path: 'composite' });
    return sse([
      toolCall(0, 'call_ok_a', argsA),
      toolCall(1, 'call_ok_b', argsB),
      needTools,
      DONE,
    ]);
  };
}
"""
)


# ── T4：跨端点工具（相关性 / 期权墙） ─────────────────────────────────────
# 假端点只造三个标的小矩阵 / 一个标的的快照：断言只看「行为与字段存在性」，
# 不依赖实现的内部函数名，也不依赖真实产物里有多少个标的。
CORRELATION_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    "  window.__CROSS_HIT = 0;\n"
    + _JS_TOOL_PRELUDE
    + r"""
  // 真实端点是 22×22（名字在 assets，数字在 matrix）；这里缩到 3 个：
  // SPX 与 BTC 正相关 0.61、与 TLT 负相关 -0.42。断言只认「名字 + 系数」同时出现。
  const CROSS = {
    as_of: '2026-10-21',
    window: 30,
    assets: [
      { name: 'SPX', group: 'equity', label: '标普500' },
      { name: 'TLT', group: 'bond', label: '长期国债' },
      { name: 'BTC', group: 'crypto', label: '比特币' },
    ],
    groups: { equity: '美股', bond: '国债', crypto: '加密' },
    matrix: [
      [1, -0.42, 0.61],
      [-0.42, 1, -0.18],
      [0.61, -0.18, 1],
    ],
    alerts: [
      {
        key: 'SPX_TLT_30d',
        label: '股债相关性（60/40 分散化有效性）',
        latest: -0.42,
        prev: -0.35,
        series: [
          { date: '2026-10-20', value: -0.35 },
          { date: '2026-10-21', value: -0.42 },
        ],
      },
    ],
  };
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/cross-asset') >= 0) {
      window.__CROSS_HIT += 1;
      return json(CROSS);
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('SPX 与 TLT 的 30 日相关性 -0.42（反向），与 BTC 为 0.61。'), stop, DONE]);
    }
    return sse([
      toolCallNamed('query_correlation', 0, 'call_corr', JSON.stringify({ symbol: 'SPX' })),
      needTools,
      DONE,
    ]);
  };
}
"""
)

OPTIONS_UNKNOWN_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    "  window.__OPT_HIT = 0;\n"
    + _JS_TOOL_PRELUDE
    + r"""
  // 端点里只有 SPY：模型问 NOPE 时，工具必须从这份数据里拿到「可用清单」再报错。
  const OPTIONS = {
    date: '2026-10-22T00:00:00Z',
    symbols: {
      SPY: {
        spot: 763.25,
        net_gex: -1.46,
        call_wall: { strike: 770, per_point_m: 70.7 },
        put_wall: { strike: 760, per_point_m: -206.5 },
      },
    },
  };
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/assets/equities-options') >= 0) {
      window.__OPT_HIT += 1;
      return json(OPTIONS);
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('快照里没有 NOPE，只有 SPY：call wall 770 / put wall 760。'), stop, DONE]);
    }
    return sse([
      toolCallNamed('query_options_wall', 0, 'call_opt', JSON.stringify({ symbol: 'NOPE' })),
      needTools,
      DONE,
    ]);
  };
}
"""
)


def _last_tool_messages(reqs: list[dict]) -> list[dict]:
    """最后一次请求里回填的 tool 消息（即本轮实际交给模型的取数结果）。"""
    if not reqs:
        return []
    return [m for m in (reqs[-1].get("messages") or []) if m.get("role") == "tool"]


def _json_object(text) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _available_names(payload: dict) -> list[str] | None:
    """错误里的「可用清单」：优先认 available，否则取任意「全是字符串的数组」。

    实现可能把清单叫 available / symbols / choices，这里不认死键名，只认形状
    （至少一个字符串元素，且整条数组都是字符串——数字数组不算清单）。
    """
    direct = payload.get("available")
    if isinstance(direct, list) and direct and all(isinstance(x, str) for x in direct):
        return direct
    stack: list[object] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            if node and all(isinstance(x, str) for x in node):
                return node
            stack.extend(node)
    return None


def _meta_without_names(meta: str, endpoints: list[str]) -> str:
    """meta 去掉端点名后的残差：用于断言「除端点名之外还有额外标记」，不认死文案。"""
    out = meta
    for endpoint in endpoints:
        out = out.replace(endpoint, "")
    return "".join(out.split())


async def check_tool_slow_endpoint(browser, base: str) -> None:
    """T3-A 慢端点：GET 拖 4.5s（> 单次 3.5s，< 8s 批次）→ 单次超时降级，本轮不卡死。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(SLOW_PATCH)
    await page.locator(".ai-input").fill("验收慢端点")
    await page.locator("[data-send]").click()
    # 单次超时（可能重试一次）比默认 6s 长，给到 15s；仍靠轮询按钮状态，不硬等
    await wait_idle(page, 15000)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__SLOW_HIT")
    check(hit >= 1, "慢端点确实被请求过（场景生效）")
    check(
        (await page.locator("[data-send]").inner_text()).strip() == "发送",
        "慢端点这一轮回到可发送状态（按钮 = 发送，对话没卡死）",
    )
    tools = _last_tool_messages(reqs)
    check(len(tools) == 1, "超时后仍回填一条 tool 结果（没把整轮对话打断）")
    payload = _json_object((tools[0] if tools else {}).get("content")) or {}
    check(bool(payload), "慢端点的 tool 结果 content 可解析出 JSON 对象")
    error = payload.get("error")
    check(
        isinstance(error, str) and bool(error.strip()),
        "tool 结果带非空 error 字段（单次超时收敛成结构化错误）",
    )
    check(payload.get("endpoint") == "rates/yield-curve", "结构化错误标明是哪个端点超时")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check(
        "4.2" in body and "接口没有返回内容" not in body,
        "最终正文照常渲染、不是空白",
    )
    await ctx.close()


async def check_tool_dup_calls(browser, base: str) -> None:
    """T3-B 同轮重复调用：同端点 + 同 json_path 的两个 id → 只发一次 GET，结果各自回填。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(DUP_PATCH)
    await page.locator(".ai-input").fill("验收同轮重复调用")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__TOOL_HIT")
    tools = _last_tool_messages(reqs)
    check(hit == 1, "同轮两次相同查询只发 1 次 /api/rates/yield-curve（同轮缓存）")
    check(len(tools) == 2, "两条 tool 结果都回填给模型")
    contents = [m.get("content") for m in tools]
    check(
        len(contents) == 2 and contents[0] == contents[1],
        "两条 tool 结果的 content 完全相同（命中的是同一份结果）",
    )
    ids = [m.get("tool_call_id") for m in tools]
    check(
        sorted(ids) == ["call_dup_a", "call_dup_b"], "两个 tool_call_id 分别对得上（不是同一个 id）"
    )
    messages = (reqs[-1].get("messages") or []) if reqs else []
    calls = [call.get("id") for m in messages for call in (m.get("tool_calls") or [])]
    check(sorted(calls) == sorted(ids), "回填的 assistant.tool_calls 与两条 tool 结果 id 一一对应")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("33bp" in body, "最终正文照常渲染")
    await ctx.close()


async def check_tool_all_fail(browser, base: str) -> None:
    """T3-C 全部端点 404：两次都是结构化错误，正文照出，meta 与成功形状不同。"""
    endpoints = ["rates/yield-curve", "credit/stress"]
    ctx, page = await new_page(browser, base)
    await page.evaluate(FAIL_PATCH)
    await page.locator(".ai-input").fill("验收两个端点都 404")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)

    reqs = await page.evaluate("() => window.__REQS")
    tools = _last_tool_messages(reqs)
    payloads = [_json_object(m.get("content")) or {} for m in tools]
    check(len(tools) == 2, "两次工具调用都回填了结果")
    check(
        len(payloads) == 2 and all(p.get("status") == 404 for p in payloads),
        "两次 tool 结果都是结构化错误（status === 404）",
    )
    check(
        {p.get("endpoint") for p in payloads} == set(endpoints),
        "两条错误各自标明端点（没有串成一个）",
    )
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("本期未导出" in body, "正文照常渲染（模型说本期未导出）")
    meta_fail = await page.locator(".ai-msg-assistant .ai-msg-meta").last.inner_text()
    check(
        all(endpoint in meta_fail for endpoint in endpoints),
        "失败轮的 meta 仍回显两个端点名",
    )
    await ctx.close()

    # 控制组：同样两个端点、同样两次调用，但都成功 → meta 形状必须与失败那次不同。
    # 不写死「未取到 / 失败」这类文案，只比较「同一个页面、同样端点」的两种结果。
    ctx, page = await new_page(browser, base)
    await page.evaluate(OK_PATCH)
    await page.locator(".ai-input").fill("验收两个端点都成功")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)
    meta_ok = await page.locator(".ai-msg-assistant .ai-msg-meta").last.inner_text()
    await ctx.close()

    check(meta_fail != meta_ok, "失败端点的 meta 与成功端点不同（失败与成功不能长得一样）")
    check(
        _meta_without_names(meta_fail, endpoints) != _meta_without_names(meta_ok, endpoints),
        "meta 在端点名之外还有额外标记（失败标记，不认死文案）",
    )


async def check_correlation_tool(browser, base: str) -> None:
    """T4-A 相关性工具：矩阵在客户端连上名字 → 回填可读的系数行，不是 3×3 裸数字。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(CORRELATION_PATCH)
    await page.locator(".ai-input").fill("验收相关性工具")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__CROSS_HIT")
    tools = _last_tool_messages(reqs)
    content = (tools[0] if tools else {}).get("content") or ""
    payload = _json_object(content) or {}
    check(hit >= 1, "相关性工具请求了 /api/cross-asset（不是本地硬编码的表）")
    check(len(tools) == 1 and bool(payload), "回填的 tool 结果是可 JSON.parse 的对象")
    # 这条是本组的核心：只回数字（matrix / matrix.0 那种）说明没做「下标→名字」连接
    check(
        "TLT" in content and "0.42" in content,
        "结果里标的名字与对应系数同时出现（不是 3×3 裸数字）",
    )
    check("SPX" in content, "结果回显被查询的标的（模型能对上问的是谁）")
    check(len(content) <= 2000, "相关性结果 ≤2000 字（单次工具结果上限）")
    check(len(reqs) == 2, "工具往返后照常发起续流请求（第二轮请求）")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("-0.42" in body and "接口没有返回内容" not in body, "最终正文渲染出相关性结论")
    await ctx.close()


async def check_options_tool_unknown_symbol(browser, base: str) -> None:
    """T4-B 期权工具查未知标的：结构化错误带可用清单，且整轮对话不断。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(OPTIONS_UNKNOWN_PATCH)
    await page.locator(".ai-input").fill("验收期权工具未知标的")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__OPT_HIT")
    tools = _last_tool_messages(reqs)
    content = (tools[0] if tools else {}).get("content") or ""
    payload = _json_object(content) or {}
    check(hit >= 1, "期权工具请求了 /api/assets/equities-options（可用清单来自端点）")
    check(len(tools) == 1, "未知标的后仍回填一条 tool 结果（没有把整轮打断）")
    error = payload.get("error")
    check(
        isinstance(error, str) and bool(error.strip()),
        "未知标的返回结构化错误（JSON 里有非空 error，不是抛异常）",
    )
    names = _available_names(payload)
    check(
        bool(names) and "SPY" in names,
        "错误里带可用清单，且含端点里真实存在的 SPY",
    )
    check(len(reqs) == 2, "未知标的照常发起续流请求（第二轮请求）")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check(
        "770" in body and "接口没有返回内容" not in body, "最终正文照常渲染（模型改答 SPY 的墙位）"
    )
    await ctx.close()


OPTIONS_OK_PATCH = (
    "() => {\n"
    "  window.__REQS = [];\n"
    "  window.__OPT_HIT = 0;\n"
    + _JS_TOOL_PRELUDE
    + r"""
  // 正向用例：SPY 带齐结论字段，另外塞三坨绘图/散文噪声（wall/gex_dist/narrative），
  // 断言它们不会出现在回填给模型的文本里。
  const OPTIONS = {
    date: '2026-10-22T00:00:00Z',
    symbols: {
      SPY: {
        symbol: 'SPY',
        name: 'SPY',
        spot: 763.25,
        net_gex: -1.46,
        net_dex: 87.81,
        pcr_oi: 2.65,
        pcr_oi_atm: 1.61,
        gamma_flip: null,
        flip_dist: null,
        call_wall: { strike: 770, per_point_m: 70.7 },
        put_wall: { strike: 760, per_point_m: -206.5 },
        iv_front: { dte: 0, iv: 13.06 },
        iv_back: { dte: 333, iv: 22.67 },
        buckets: { '0DTE': { gex_pct: 10, gex_m: -214.7, contracts: 389927 } },
        oi_top: [
          { strike: 700, total: 100 },
          { strike: 760, total: 75524 },
          { strike: 800, total: 300 }
        ],
        iv_curve: [{ dte: 3, iv: 12 }, { dte: 0, iv: 13.06 }, { dte: 30, iv: 18 }],
        wall: Array.from({ length: 50 }, (_, i) => ({ strike: 600 + i, gex_m: i * 1.5 })),
        gex_dist: Array.from({ length: 50 }, (_, i) => ({ strike: 600 + i, gex_m: i })),
        narrative: {
          gamma: { title: '负 Gamma：波动更容易放大', text: '现价位于 Gamma Flip 下方' }
        }
      },
    },
  };
  const orig = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/assets/equities-options') >= 0) {
      window.__OPT_HIT += 1;
      return json(OPTIONS);
    }
    if (u.indexOf('/api/chat') < 0) return orig(url, opts);
    const body = chatBody(opts);
    window.__REQS.push(body);
    if (hasToolMsg(body)) {
      return sse([text('SPY 快照：call wall 770、put wall 760、spot 763.25。'), stop, DONE]);
    }
    return sse([
      toolCallNamed('query_options_wall', 0, 'call_opt', JSON.stringify({ symbol: 'SPY' })),
      needTools,
      DONE,
    ]);
  };
}
"""
)


async def check_options_tool_summary(browser, base: str) -> None:
    """T4-B 正向：已知标的返回压缩摘要（含结论字段、不含绘图明细、≤2000 字）。"""
    ctx, page = await new_page(browser, base)
    await page.evaluate(OPTIONS_OK_PATCH)
    await page.locator(".ai-input").fill("验收期权工具正向")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)

    reqs = await page.evaluate("() => window.__REQS")
    hit = await page.evaluate("() => window.__OPT_HIT")
    tools = _last_tool_messages(reqs)
    content = (tools[0] if tools else {}).get("content") or ""
    payload = _json_object(content) or {}
    check(hit >= 1, "期权工具请求了 /api/assets/equities-options")
    check(bool(payload), "回填的 tool 结果是一段可解析的 JSON 对象")
    check(payload.get("spot") == 763.25, "摘要里带 spot（结论读数没被摘要吃掉）")
    check((payload.get("call_wall") or {}).get("strike") == 770, "摘要里带 call wall 行权价")
    check((payload.get("put_wall") or {}).get("strike") == 760, "摘要里带 put wall 行权价")
    check(
        all(k not in content for k in ('"wall"', '"gex_dist"', '"narrative"')),
        "绘图明细与规则引擎散文没有进上下文（wall / gex_dist / narrative）",
    )
    check(len(content) <= 2000, "压缩后不超过 2000 字上限")
    oi = payload.get("oi_top") or []
    check(
        [x.get("total") for x in oi] == sorted([x.get("total") for x in oi], reverse=True),
        "oi_top 按 total 降序（不是按端点给的顺序切前 5）",
    )
    check(len(reqs) == 2, "取数后照常发起续流请求")
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("770" in body, "最终正文照常渲染")
    await ctx.close()


async def check_mobile_fab(browser, base: str) -> None:
    """窄屏浮钮：40px / 贴边只露 24 / 点一下弹全并开抽屉 / 拖拽换边 / 滚动隐去。"""
    ctx = await browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )
    page = await ctx.new_page()
    await page.goto(base + "assets/", wait_until="domcontentloaded")
    await page.wait_for_timeout(1200)

    vw = 390
    box = await page.locator(".ai-float").bounding_box()
    check(abs(box["width"] - 40) < 1, "窄屏浮钮 40px（44 → 40）")
    check(20 <= vw - box["x"] <= 28, "默认贴右边、只露约 24px（半吸附）")

    await page.locator(".ai-float").click()
    await page.wait_for_timeout(400)
    opened = await page.locator(".ai-drawer.open").count() == 1
    box2 = await page.locator(".ai-float").bounding_box()
    check(opened, "点一下打开抽屉")
    check(box2["x"] + box2["width"] <= vw + 1, "点开后浮钮弹到全可见")
    await page.locator('[data-act="close"]').first.click()
    await page.wait_for_timeout(400)
    box3 = await page.locator(".ai-float").bounding_box()
    check(20 <= vw - box3["x"] <= 28, "关抽屉后重新吸回边缘")

    cx, cy = box3["x"] + box3["width"] / 2, box3["y"] + box3["height"] / 2
    await page.mouse.move(cx, cy)
    await page.mouse.down()
    for i in range(1, 13):
        await page.mouse.move(cx - (cx - 120) * i / 12, cy - 200 * i / 12)
    await page.mouse.up()
    await page.wait_for_timeout(400)
    box4 = await page.locator(".ai-float").bounding_box()
    check(box4["x"] < 0, "拖到左半边松手后吸附左侧（负 x）")

    await page.evaluate("window.scrollTo(0, 900)")
    await page.wait_for_timeout(100)
    cls = await page.evaluate(
        "() => [document.querySelector('.ai-float').className,"
        " (document.querySelector('.back-top') || {className: ''}).className]"
    )
    check("scroll-away" in cls[0], "滚动中伴读浮钮隐去")
    check("scroll-away" in cls[1], "滚动中「返回顶部」一起隐去")
    await page.wait_for_timeout(500)
    cls2 = await page.evaluate(
        "() => [document.querySelector('.ai-float').className,"
        " (document.querySelector('.back-top') || {className: ''}).className]"
    )
    check("scroll-away" not in cls2[0] and "scroll-away" not in cls2[1], "停手后两者一起浮回")

    # 返回顶部：带文字标签、静止在右下、不跟浮钮跑（NN/g：Label it + Stationary）
    bt = await page.locator(".back-top").bounding_box()
    label = await page.evaluate(
        "() => { const b = document.querySelector('.back-top');"
        " return (b.getAttribute('aria-label') || '') + '|' + b.title; }"
    )
    check("返回顶部" in label, "「返回顶部」视觉只有箭头，但有 aria-label / title 兜语义")
    check(bt["x"] + bt["width"] > vw - 30, "「返回顶部」仍贴右下角")
    # 浮钮拖到左边后，返回顶部不该跟着动
    fab_left = await page.locator(".ai-float").bounding_box()
    check(fab_left["x"] < 0, "浮钮已在左侧吸附（对照组）")
    check(bt["x"] > vw / 2, "浮钮拖走后「返回顶部」仍在右下，没有跟着跑")
    await ctx.close()


async def check_retry(browser, base: str) -> None:
    """断流重试：网络抖动自动重发、持续失败留手动重试、429 按 Retry-After 收手、退避可停止。"""
    # 1) 前两次断网、第三次成功 → 自动重试兜住，用户只看到答案
    ctx, page = await new_page(browser, base)
    hits = {"n": 0}

    async def flaky(route):
        hits["n"] += 1
        if hits["n"] < 3:
            await route.abort("failed")
            return
        await route.fulfill(
            status=200,
            headers={"content-type": "text/event-stream", "x-llm-model": "deepseek-flash"},
            body=SSE,
        )

    await page.route("**/api/chat", flaky)
    await page.locator(".ai-input").fill("断流重试验收")
    await page.locator("[data-send]").click()
    await wait_idle(page, 12000)
    body = await page.locator(".ai-msg-assistant").last.inner_text()
    check("验收答" in body, "断网两次后自动重试成功给出答案")
    check(hits["n"] == 3, "断网共发 3 次请求（首次 + 2 次自动重试）")
    await ctx.close()

    # 2) 一直 502 → 错误文案带重试次数；手动「重试」还能再发一轮
    ctx, page = await new_page(browser, base)
    down = {"n": 0}

    async def fail502(route):
        down["n"] += 1
        await route.fulfill(
            status=502,
            content_type="application/json",
            body='{"error":"代理失败：上游 connect timeout"}',
        )

    await page.route("**/api/chat", fail502)
    await page.locator(".ai-input").fill("持续失败验收")
    await page.locator("[data-send]").click()
    await wait_idle(page, 12000)
    text = await page.locator(".ai-msg-assistant").last.inner_text()
    check("已自动重试 2 次" in text, "持续失败时错误文案说明已自动重试几次")
    before = down["n"]
    await page.locator('[data-mact="retry"]').click()
    await wait_idle(page, 12000)
    check(down["n"] == before + 3, "手动「重试」真的重发（再走一轮 3 次尝试）")
    await ctx.close()

    # 3) 429 + Retry-After 60：不自动等（等 60 秒 = 抽屉钉死），文案说明要等多久
    ctx, page = await new_page(browser, base)
    limited = {"n": 0}

    async def tooMany(route):
        limited["n"] += 1
        await route.fulfill(
            status=429,
            headers={"content-type": "application/json", "retry-after": "60"},
            body='{"error":"请求过于频繁（本站代理每分钟 10 次），请稍后再试。"}',
        )

    await page.route("**/api/chat", tooMany)
    await page.locator(".ai-input").fill("限流验收")
    await page.locator("[data-send]").click()
    await wait_idle(page, 12000)
    text = await page.locator(".ai-msg-assistant").last.inner_text()
    check(limited["n"] == 1, "Retry-After 超过退避上限时不再自动重试")
    check("服务端要求 60 秒后再试" in text, "限流文案告诉用户多久后再来")
    await ctx.close()

    # 4) 退避期间点「停止」立即收手（不能干等退避）
    ctx, page = await new_page(browser, base)

    async def maintenance(route):
        await route.fulfill(status=503, content_type="application/json", body='{"error":"维护中"}')

    await page.route("**/api/chat", maintenance)
    await page.locator(".ai-input").fill("退避中停止验收")
    await page.locator("[data-send]").click()
    await page.wait_for_timeout(300)
    send_label = (await page.locator("[data-send]").inner_text()).strip()
    check(send_label == "停止", "退避期间发送按钮仍是「停止」（可以打断）")
    await page.locator("[data-send]").click()
    await wait_idle(page, 8000)
    check("（已停止）" in await page.locator(".ai-msgs").inner_text(), "退避期间点停止立刻收手")
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
        await check_tool_slow_endpoint(browser, base)
        await check_tool_dup_calls(browser, base)
        await check_tool_all_fail(browser, base)
        await check_correlation_tool(browser, base)
        await check_options_tool_unknown_symbol(browser, base)
        await check_options_tool_summary(browser, base)
        await check_mobile_fab(browser, base)
        await check_save_note(browser, base)
        await check_retry(browser, base)
        await browser.close()
    print(f"\n{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    raise SystemExit(asyncio.run(main(target)))
