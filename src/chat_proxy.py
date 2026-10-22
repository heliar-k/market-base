"""伴读抽屉的 LLM 代理配置与请求校验（本地 FastAPI 侧）。

线上 Pages Functions 有一份同契约的 JS 实现（functions/_lib/common.js）；
两边共用 docs/ai-chat-backend.md 里的限额表，改限额时记得同步两处。
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_TOKENS = 1200
MAX_MESSAGES = 30
MAX_CHARS = 40_000
# HTTP body 上限按字节算：40k 中文字符 ≈120KB UTF-8，留出 JSON 包装余量
BODY_MAX = 256 * 1024
ROLES = {"system", "user", "assistant"}
EFFORTS = {"minimal", "low", "medium", "high"}

MISSING_KEY = (
    "本站代理未配置 LLM_API_KEY：请在服务端环境变量里设置"
    "（本地 .env / 线上 Cloudflare Pages 项目），或点右上「⚙ 设置」填自己的 Key 直连。"
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
    try:
        return max(64, min(8000, int(os.environ.get("LLM_CHAT_MAX_TOKENS", MAX_TOKENS))))
    except ValueError:
        return MAX_TOKENS


def allow_effort() -> bool:
    return os.environ.get("LLM_CHAT_ALLOW_EFFORT", "").lower() in {"1", "true", "yes"}


def validate_messages(items: list[tuple[str, str]]) -> list[dict[str, str]]:
    """校验 (role, content) 列表；条数/总字符/角色不合规直接抛 ChatValidationError。"""
    if not items:
        raise ChatValidationError(422, "messages 不能为空")
    if len(items) > MAX_MESSAGES:
        raise ChatValidationError(413, f"messages 最多 {MAX_MESSAGES} 条")
    total = 0
    out: list[dict[str, str]] = []
    for role_raw, content_raw in items:
        role = (role_raw or "").strip()
        if role not in ROLES:
            raise ChatValidationError(422, f"非法 role：{role or '(空)'}")
        content = content_raw or ""
        total += len(content)
        out.append({"role": role, "content": content})
    if total > MAX_CHARS:
        raise ChatValidationError(413, f"消息总长度超过 {MAX_CHARS} 字")
    if not any(m["role"] == "user" for m in out):
        raise ChatValidationError(422, "messages 里至少要有一条 user 消息")
    return out


def build_payload(
    requested_model: str | None,
    messages: list[dict[str, str]],
    requested_effort: str | None,
) -> dict[str, Any]:
    """构造上游请求体；effort 只在服务端显式开启时才透传。"""
    payload: dict[str, Any] = {
        "model": resolve_model(requested_model),
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens(),
    }
    effort = (requested_effort or "").strip()
    if allow_effort() and effort in EFFORTS:
        payload["reasoning_effort"] = effort
    return payload
