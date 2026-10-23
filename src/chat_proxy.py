"""伴读抽屉的 LLM 代理配置与请求校验（本地 FastAPI 侧）。

线上 Pages Functions 有一份同契约的 JS 实现（functions/_lib/common.js）；
两边共用 docs/ai-chat-backend.md 里的限额表，改限额时记得同步两处。
"""

from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_BASE = "https://api.deepseek.com"
# deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用（官方更新日志），
# 现行枚举只剩 deepseek-flash（思考/非思考同体，靠 thinking 字段切换）与 deepseek-v4-pro。
DEFAULT_MODEL = "deepseek-flash"
MAX_TOKENS = 1200
# 思考模式下 reasoning token 计入 max_tokens：1200 会被思维链吃光、正文被截断
MAX_TOKENS_THINKING = 4000
MAX_MESSAGES = 30
MAX_CHARS = 40_000
# 工具相关限额与 functions/_lib/common.js 的同名常量逐条对齐（改一处必须改两处）
MAX_TOOLS = 8
MAX_TOOL_CALLS = 8
TOOL_DESC_MAX = 500
TOOL_CALL_ID_MAX = 128
TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
TOOL_CHOICES = {"auto", "none"}
# HTTP body 上限按字节算：40k 中文字符 ≈120KB UTF-8，留出 JSON 包装余量
BODY_MAX = 256 * 1024
ROLES = {"system", "user", "assistant", "tool"}
EFFORTS = {"minimal", "low", "medium", "high"}

MISSING_KEY = (
    "本站代理未配置 LLM_API_KEY：请在服务端环境变量里设置"
    "（本地 .env / 线上 Cloudflare Pages 项目），或点输入框上方的「⋯ → ⚙ 设置」填自己的 Key 直连。"
)


class ChatValidationError(ValueError):
    """带 HTTP 状态码的入参校验错误（route 直接映射成 {error}）。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def llm_key() -> str:
    return os.environ.get("LLM_API_KEY", "").strip()


def llm_base() -> str:
    return (os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE).rstrip("/")


def chat_models() -> list[str]:
    """允许走代理的模型（服务端白名单；默认只有 LLM_MODEL 一个）。"""
    raw = os.environ.get("LLM_CHAT_MODELS", "").strip() or os.environ.get("LLM_MODEL", "").strip()
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or [DEFAULT_MODEL]


def resolve_model(requested: str | None) -> str:
    allowed = chat_models()
    return requested if requested in allowed else allowed[0]


def max_tokens() -> int:
    raw = os.environ.get("LLM_CHAT_MAX_TOKENS", "").strip()
    if raw:
        try:
            return max(64, min(8000, int(raw)))
        except ValueError:
            pass
    return MAX_TOKENS_THINKING if thinking_mode() == "enabled" else MAX_TOKENS


def allow_effort() -> bool:
    return os.environ.get("LLM_CHAT_ALLOW_EFFORT", "").lower() in {"1", "true", "yes"}


def thinking_mode() -> str | None:
    """思考模式档位：'enabled' | 'disabled' | None（None = 不下发该字段）。

    DeepSeek 侧默认开启并显式写进请求体——上游默认值会变，客户端不该依赖它。
    `LLM_THINKING=off` 可关掉（`src/llm.py` 的叙事改写共用同一个开关）。
    别的 OpenAI 兼容网关不认 `thinking` 字段，一律不下发。
    """
    if "api.deepseek.com" not in llm_base():
        return None
    return (
        "disabled"
        if os.environ.get("LLM_THINKING", "").strip().lower() in {"0", "false", "no", "off"}
        else "enabled"
    )


def _is_object(value: Any) -> bool:
    """是「对象」而非 None / 数组（JSON Schema 的 parameters 只要求到这里）。"""
    return isinstance(value, dict)


def validate_tools(items: Any) -> list[dict[str, Any]] | None:
    """校验 tools（可选）：≤8 个、type 只准 function、name 形状、description ≤500、
    parameters 是对象。**不校验 JSON Schema 内容**，通过后原样返回（不裁字段）。

    空数组视同未传（避免上游对 `tools: []` 的行为不确定）。
    """
    if items is None:
        return None
    if not isinstance(items, list):
        raise ChatValidationError(422, "tools 必须是数组")
    if len(items) > MAX_TOOLS:
        raise ChatValidationError(422, f"tools 最多 {MAX_TOOLS} 个")
    if not items:
        return None
    for i, tool in enumerate(items):
        at = f"tools[{i}]"
        if not _is_object(tool):
            raise ChatValidationError(422, f"{at} 必须是对象")
        if tool.get("type") != "function":
            raise ChatValidationError(422, f'{at}.type 只支持 "function"')
        fn = tool.get("function")
        if not _is_object(fn):
            raise ChatValidationError(422, f"{at}.function 必须是对象")
        name = fn.get("name")
        if not isinstance(name, str) or not TOOL_NAME_RE.fullmatch(name):
            raise ChatValidationError(422, f"{at}.function.name 必须是 1-64 位字母/数字/_/-")
        desc = fn.get("description")
        if not isinstance(desc, str):
            raise ChatValidationError(422, f"{at}.function.description 必须是字符串")
        if len(desc) > TOOL_DESC_MAX:
            raise ChatValidationError(422, f"{at}.function.description 最多 {TOOL_DESC_MAX} 字")
        if not _is_object(fn.get("parameters")):
            raise ChatValidationError(422, f"{at}.function.parameters 必须是对象")
    return items


def validate_tool_choice(value: Any) -> str | None:
    """校验 tool_choice（可选）：只认 "auto" / "none"。

    `required` 与具名函数在思考模式下会被上游 400
    （雷火网关：Thinking mode does not support this tool_choice），所以入口就拒掉。
    """
    if value is None:
        return None
    if not isinstance(value, str) or value not in TOOL_CHOICES:
        raise ChatValidationError(
            422, 'tool_choice 只支持 "auto" / "none"（思考模式下上游不支持 required / 具名函数）'
        )
    return value


def validate_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验消息列表并归一化（role / content / tool_call_id / tool_calls）。

    - `tool` 角色必须带 `tool_call_id`（≤128 字）。
    - `assistant` 可带 `tool_calls`（≤8 条），**原样透传**（不重排、不裁字段）。
    - 40k 上限仍按 content 累加，`tool_calls[].function.arguments` 也计入（否则可绕过）。
    - `content` 允许 null / 缺失（assistant 只带工具调用时上游就发 null），归一成 ""。
    """
    if not items:
        raise ChatValidationError(422, "messages 不能为空")
    if len(items) > MAX_MESSAGES:
        raise ChatValidationError(413, f"messages 最多 {MAX_MESSAGES} 条")
    total = 0
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ChatValidationError(422, "非法消息：每条必须是对象")
        role = str(raw.get("role") or "").strip()
        if role not in ROLES:
            raise ChatValidationError(422, f"非法 role：{role or '(空)'}")
        content_raw = raw.get("content")
        if content_raw is not None and not isinstance(content_raw, str):
            raise ChatValidationError(422, "content 必须是字符串或 null")
        content = content_raw or ""
        total += len(content)
        msg: dict[str, Any] = {"role": role, "content": content}
        if role == "tool":
            call_id = raw.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ChatValidationError(422, "tool 角色消息必须带 tool_call_id")
            if len(call_id) > TOOL_CALL_ID_MAX:
                raise ChatValidationError(422, f"tool_call_id 最多 {TOOL_CALL_ID_MAX} 字")
            msg["tool_call_id"] = call_id
        if role == "assistant" and raw.get("tool_calls") is not None:
            calls = raw["tool_calls"]
            if not isinstance(calls, list):
                raise ChatValidationError(422, "assistant.tool_calls 必须是数组")
            if len(calls) > MAX_TOOL_CALLS:
                raise ChatValidationError(422, f"assistant.tool_calls 最多 {MAX_TOOL_CALLS} 条")
            for call in calls:
                if not isinstance(call, dict):
                    raise ChatValidationError(422, "tool_calls 每一项必须是对象")
                fn = call.get("function")
                args = fn.get("arguments") if isinstance(fn, dict) else None
                if args is None:
                    continue
                if not isinstance(args, str):
                    raise ChatValidationError(422, "tool_calls[].function.arguments 必须是字符串")
                total += len(args)
            msg["tool_calls"] = calls  # 原样透传：不重排、不裁字段
        out.append(msg)
    if total > MAX_CHARS:
        raise ChatValidationError(413, f"消息总长度超过 {MAX_CHARS} 字")
    if not any(m["role"] == "user" for m in out):
        raise ChatValidationError(422, "messages 里至少要有一条 user 消息")
    return out


def build_payload(
    requested_model: str | None,
    messages: list[dict[str, Any]],
    requested_effort: str | None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> dict[str, Any]:
    """构造上游请求体；effort 只在服务端显式开启时才透传。

    `tools` / `tool_choice` 由路由层校验后传入，这里原样下发——工具循环在前端，
    服务端只负责把通道打通，不解析 SSE、不改写消息。
    """
    payload: dict[str, Any] = {
        "model": resolve_model(requested_model),
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens(),
    }
    effort = (requested_effort or "").strip()
    if allow_effort() and effort in EFFORTS:
        payload["reasoning_effort"] = effort
    if thinking_mode():
        payload["thinking"] = {"type": thinking_mode()}
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    return payload
