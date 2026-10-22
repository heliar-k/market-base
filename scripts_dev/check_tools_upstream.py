"""上游能力实测：思考模式 + function tools 的六项检查（T1 开工前置）。

用法：
    uv run python scripts_dev/check_tools_upstream.py
    uv run python scripts_dev/check_tools_upstream.py --model ai-leihuo-netease/deepseek-flash

读 `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY`，模型默认取 `LLM_MODEL`（缺省 `deepseek-flash`）。
不写任何东西，只打印结论表，`exit 1` 表示有必过项失败。

为什么要有这个脚本：`docs/SPEC-ai-drawer-tools.md` 第 6 条的「带 tools 的请求必须回传
`reasoning_content`，否则 400」是一条**上游行为**，换地址 / 换 key / 网关改版都会变。
每换一次上游就跑一次，别按文档猜（2026-10-22 实测：经本地网关（magpie → 雷火 → deepseek
兼容链）不强制，见 R6）。

六项：
  R1 模型表可达（目标 id 在不在表里只作提示，裸名别名也能调）
  R2 非流式基础对话回 `reasoning_content`（思考模式真的开着）
  R3 带 tools 的请求能触发 `tool_calls` + `finish_reason: "tool_calls"`
  R4 流式分片能把 `arguments` 拼回一个合法 JSON（循环要靠这个）
  R5 回填 `tool` 结果后能基于结果作答（完整往返）
  R6 历史 assistant 缺 `reasoning_content` + 带 tools → 400 还是 200（A/B 方案的判据）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_site_data",
            "description": "查询本站已导出的数据端点，返回 JSON。页面摘要里没有的读数用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string", "enum": ["rates/yield-curve"]},
                    "json_path": {"type": "string"},
                },
                "required": ["endpoint"],
            },
        },
    }
]

ASK = "用 query_site_data 查 rates/yield-curve 的 30Y 收益率，不要凭记忆回答。"


def load_env() -> tuple[str, str, str]:
    """从环境变量取配置，缺的再从仓库根 `.env` 补（与 src/config.py 同口径）。"""
    vals: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    base = (os.environ.get("LLM_BASE_URL") or vals.get("LLM_BASE_URL") or "").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or vals.get("LLM_API_KEY") or ""
    model = os.environ.get("LLM_MODEL") or vals.get("LLM_MODEL") or "deepseek-flash"
    return base, key, model


def post(base: str, key: str, body: dict, timeout: int = 90) -> tuple[int, str]:
    """POST /chat/completions，HTTP 错误也返回 (status, body) 而不是抛。"""
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def get_models(base: str, key: str) -> tuple[int, list[str]]:
    req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return resp.status, [str(m.get("id")) for m in data.get("data", [])]
    except urllib.error.HTTPError as exc:
        return exc.code, []


def parse_stream(raw: str) -> dict:
    """把 SSE 拆成「正文 / 思维链 / 工具参数分片」，模拟浏览器侧 runTurn 的累积逻辑。"""
    content: list[str] = []
    think: list[str] = []
    args = ""
    call_id = ""
    finish = ""
    fragments = 0
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            choice = json.loads(payload)["choices"][0]
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
        delta = choice.get("delta") or {}
        content.append(delta.get("content") or "")
        think.append(delta.get("reasoning_content") or "")
        for frag in delta.get("tool_calls") or []:
            fragments += 1
            call_id = call_id or str(frag.get("id") or "")
            args += (frag.get("function") or {}).get("arguments") or ""
        finish = choice.get("finish_reason") or finish
    return {
        "content": "".join(content),
        "think": "".join(think),
        "args": args,
        "id": call_id,
        "finish": finish,
        "fragments": fragments,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="覆盖模型 id（默认取 LLM_MODEL）")
    args = ap.parse_args()

    base, key, model = load_env()
    model = args.model or model
    if not base or not key:
        print("缺少 LLM_BASE_URL / LLM_API_KEY（环境变量或仓库根 .env）", file=sys.stderr)
        return 2
    print(f"上游 = {base}\n模型 = {model}\n")

    rows: list[tuple[str, bool, str]] = []

    status, models = get_models(base, key)
    # 模型表只验可达：网关普遍接受裸名别名（如 `deepseek-flash` 不在 /models 里也能调），
    # 「这个 id 到底能不能用」由 R2 的实际调用说话，别在这里判死。
    ok = status == 200
    listed = "在册" if model in models else "不在表里（按别名解析，看 R2）"
    hint = f"{len(models)} 个模型，目标 id {listed}" if models else "模型表不可读"
    rows.append(("R1 模型表可达", ok, f"HTTP {status}，{hint}"))

    thinking = {"type": "enabled"}
    plain = {"role": "user", "content": "1+1=? 只回数字"}
    status, text = post(
        base,
        key,
        {
            "model": model,
            "stream": False,
            "max_tokens": 200,
            "thinking": thinking,
            "messages": [plain],
        },
    )
    think_len = 0
    if status == 200:
        msg = json.loads(text)["choices"][0]["message"]
        think_len = len(msg.get("reasoning_content") or "")
    rows.append(
        (
            "R2 思考模式回 reasoning_content",
            status == 200 and think_len > 0,
            f"HTTP {status}，思维链 {think_len} 字",
        )
    )

    status, text = post(
        base,
        key,
        {
            "model": model,
            "stream": False,
            "max_tokens": 500,
            "thinking": thinking,
            "tools": TOOLS,
            "messages": [{"role": "user", "content": ASK}],
        },
    )
    calls: list[dict] = []
    finish = ""
    if status == 200:
        choice = json.loads(text)["choices"][0]
        calls = choice["message"].get("tool_calls") or []
        finish = choice.get("finish_reason") or ""
    rows.append(
        (
            "R3 带 tools 能触发 tool_calls",
            bool(calls) and finish == "tool_calls",
            f"HTTP {status}，{len(calls)} 个调用，finish={finish or '-'}",
        )
    )

    stream_body = {
        "model": model,
        "stream": True,
        "max_tokens": 500,
        "thinking": thinking,
        "tools": TOOLS,
        "messages": [{"role": "user", "content": ASK}],
    }
    status, raw = post(base, key, stream_body)
    parsed = parse_stream(raw) if status == 200 else {}
    args_ok = False
    if parsed.get("args"):
        try:
            json.loads(parsed["args"])
            args_ok = True
        except json.JSONDecodeError:
            args_ok = False
    rows.append(
        (
            "R4 流式分片能拼回合法 arguments",
            status == 200 and args_ok,
            f"HTTP {status}，{parsed.get('fragments', 0)} 个分片",
        )
    )

    round_ok = False
    round_note = "无工具调用，跳过"
    if parsed.get("id") and args_ok:
        msgs = [
            {"role": "user", "content": ASK},
            {
                "role": "assistant",
                "content": parsed.get("content") or "",
                "tool_calls": [
                    {
                        "id": parsed["id"],
                        "type": "function",
                        "function": {"name": "query_site_data", "arguments": parsed["args"]},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": parsed["id"],
                "content": json.dumps(
                    {"data": {"current": {"30Y": 4.812}}, "as_of": "2026-10-21"},
                    ensure_ascii=False,
                ),
            },
        ]
        status, raw2 = post(
            base,
            key,
            {
                "model": model,
                "stream": True,
                "max_tokens": 500,
                "thinking": thinking,
                "tools": TOOLS,
                "messages": msgs,
            },
        )
        parsed2 = parse_stream(raw2) if status == 200 else {}
        body2 = parsed2.get("content") or ""
        round_ok = status == 200 and "4.812" in body2
        round_note = f"HTTP {status}，正文 {len(body2)} 字，引用结果={round_ok}"
    rows.append(("R5 回填 tool 结果后基于结果作答", round_ok, round_note))

    history = [
        {"role": "user", "content": "1+1"},
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "1+2"},
        {"role": "assistant", "content": "3"},
        {"role": "user", "content": "刚才我问了什么？一句话。"},
    ]
    status, text = post(
        base,
        key,
        {
            "model": model,
            "stream": False,
            "max_tokens": 300,
            "thinking": thinking,
            "tools": TOOLS,
            "messages": history,
        },
    )
    verdict = "不强制（200）" if status == 200 else f"强制（{status}）"
    note = f"HTTP {status} → {verdict}"
    if status != 200:
        note += "：" + text.replace("\n", " ")[:160]
    rows.append(("R6 历史 assistant 缺 reasoning_content + tools", True, note))

    width = max(len(r[0]) for r in rows)
    print("─" * (width + 40))
    for name, ok, note in rows:
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {name.ljust(width)}  {note}")
    print("─" * (width + 40))

    must_pass = [r for r in rows if r[0] != "R6 历史 assistant 缺 reasoning_content + tools"]
    failed = [r[0] for r in must_pass if not r[1]]
    if failed:
        print("必过项失败：" + "、".join(failed))
        return 1
    print("R6 只是判据不是门槛：200 → thread schema 不用为 reasoning_content 改动；")
    print("400 → 按 SPEC 第 6 条走 B（落盘不截断）或在 runTurn 加降级重发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
