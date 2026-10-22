"""LLM 叙事生成客户端（build-time 批量调用，规则引擎兜底）。

设计见 docs/llm-narrative-design.md。要点：
- 各 *_analysis.py 的 _llm_generate() 先用规则引擎算出全部事实（facts），再调
  generate_narrative() 让 LLM 受控改写叙事段——LLM 只写字，不产数字。
- 供应商走 OpenAI 兼容协议，env 三件套决定（换 OpenAI 兼容商改 env 零代码）：
    LLM_API_KEY   必填，未填 = 未启用（各模块直接回落规则引擎）
    LLM_BASE_URL  默认 https://api.deepseek.com
    LLM_MODEL     默认 deepseek-flash（思考模式默认开，见 _thinking_mode）
- fail-open：任何故障（key 缺失 / 网络超时 / 返回非 JSON / 校验不过）返回 None，
  调用方回落规则引擎；回落时 stderr 留一行 LLM_FALLBACK 标记，
  deploy-pages.yml 的「LLM fallback → warning」step grep 这个串发 CI 告警注解
  （两侧格式约定死，改动需同步，tests/test_llm_narrative.py 有断言锁定）。
- 校验两道（宁严勿松，误杀无损——回落的是已验证的规则引擎文案）：
    ① 数字一致性：LLM 输出里的每个数字必须存在于 facts（归一化后比对）；
    ② 长度上限：每段 body ≤ max(1.5 × 输入段长, 200 字)，超长挤破卡片版式。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from src.config import ROOT

_TIMEOUT = 60  # 秒
_CACHE_TTL_DAYS = 7  # 数据日更，超过 7 天的缓存条目必已失效，写入时顺手清掉


def _thinking_mode() -> str | None:
    """思考模式档位：'enabled' | 'disabled' | None（None = 不下发该字段）。

    DeepSeek 侧默认开启并显式写进请求体——上游默认值会变，客户端不该依赖它。
    与 src/chat_proxy.py 的同名判定同口径：`LLM_THINKING=off` 可关掉（两处共用一个开关）。
    注意思考模式下 `temperature` 不生效（官方文档），叙事改写因此少一道确定性保障。
    """
    base = os.environ.get("LLM_BASE_URL", "").strip() or "https://api.deepseek.com"
    if "api.deepseek.com" not in base:
        return None
    off = os.environ.get("LLM_THINKING", "").strip().lower() in {"0", "false", "no", "off"}
    return "disabled" if off else "enabled"


_SYSTEM_PROMPT = """\
你是美股宏观研究站「观澜台」的研判撰写员，读者是懂市场的自己人。
输入是规则引擎已经算好的事实（全部数字、区间、信号标签），你的职责是把它们改写成专业简体中文研判叙事。
规则：
1. 允许重组段落、串联因果、补充不含数字的定性判断（资金面情绪、风险偏好、跨段呼应）。
2. 禁止出现输入事实之外的任何数字；禁止编造数据。
3. 不得推翻事实给出的结论方向（事实说正利差不得写成倒挂，事实说熊陡不得写牛平）。
4. 只改写 body 文字，title 保持原文不动；输出与输入同构的 JSON：
   {"sections": [{"title": "...", "body": "..."}]}
5. 每段 body 与输入对应段长度相当（不超过约 1.5 倍）——研判段落是卡片版式里的
   定长格子，超长会挤破版面。"""


def llm_enabled() -> bool:
    """env 有 key 即启用（config 已自动加载 .env）。"""
    return bool(os.environ.get("LLM_API_KEY"))


def _fallback(module: str, reason: str) -> None:
    """stderr 标记。deploy-pages.yml 的 CI 告警 grep `LLM_FALLBACK`，勿改格式。"""
    print(f"LLM_FALLBACK: {module}: {reason}", file=sys.stderr)


def _norm_num(s: str) -> str:
    """数字串归一化：去千分位、转 float、%g 压尾零（3.90 → 3.9，42.0 → 42）。"""
    return f"{float(s.replace(',', '')):g}"


def _extract_numbers(text: str) -> set[str]:
    """文本中的数字归一化集合（含绝对值形态：facts 里 -6.0 放行「收窄 6bp」的写法）。"""
    out: set[str] = set()
    for m in re.finditer(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
        n = _norm_num(m.group())
        out.add(n)
        out.add(_norm_num(str(abs(float(n))))) if n.startswith("-") else None
    return out


def validate_sections(out: list, payload: dict) -> str | None:
    """校验 LLM 输出。通过返回 None，否则返回失败原因（进 LLM_FALLBACK 日志）。

    结构：段数一致、title 逐一相同；数字：body 数字 ⊆ facts 全文数字集合；
    长度：每段 body ≤ max(1.5 × 输入段长, 200 字)。
    """
    facts_sections = payload["sections"]
    if not isinstance(out, list) or len(out) != len(facts_sections):
        return "段数与输入不一致"
    allowed = _extract_numbers(json.dumps(payload, ensure_ascii=False, default=str))
    for i, (o, inp) in enumerate(zip(out, facts_sections)):
        if not isinstance(o, dict) or o.get("title") != inp.get("title"):
            return f"第 {i + 1} 段 title 不符"
        body = o.get("body")
        if not isinstance(body, str) or not body.strip():
            return f"第 {i + 1} 段 body 缺失"
        unknown = _extract_numbers(body) - allowed
        if unknown:
            return f"第 {i + 1} 段出现事实之外的数字: {sorted(unknown)[:3]}"
        limit = max(int(len(str(inp["body"])) * 1.5), 200)
        if len(body) > limit:
            return f"第 {i + 1} 段超长（{len(body)} > {limit} 字）"
    return None


def _cache_path(module: str, payload: dict, prompt_hint: str) -> Path:
    """内容寻址缓存路径：键含 payload/提示词/模型/base_url —— 数据不变则零调用，
    数据/提示词/换商任一变化自动失效，永无陈旧文案。仅存校验通过的输出。"""
    key = hashlib.sha256(
        (
            prompt_hint
            + json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
            + os.environ.get("LLM_MODEL", "deepseek-flash")
            + os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        ).encode()
    ).hexdigest()[:24]
    return ROOT / "data" / "cache" / "llm" / module / f"{key}.json"


def _cache_prune(module: str) -> None:
    """清掉过期条目（Actions 的 actions/cache 有体积上限，别让死条目挤占）。"""
    d = ROOT / "data" / "cache" / "llm" / module
    if not d.is_dir():
        return
    cutoff = time.time() - _CACHE_TTL_DAYS * 86400
    for f in d.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def generate_narrative(module: str, payload: dict, prompt_hint: str) -> dict | None:
    """调 LLM 改写 payload["sections"] 的叙事。

    payload = {"sections": [{title, body}, ...], **额外事实（数字一并进入白名单）}。
    成功返回 {"sections": [...]}（title 原样、body 已改写），任何失败返回 None。
    内容寻址缓存：命中直接返回（不建 HTTP 连接）；未命中调 API，校验通过后写缓存。
    """
    cache = _cache_path(module, payload, prompt_hint)
    if cache.is_file():
        try:
            out = json.loads(cache.read_text())
            if validate_sections(out["sections"], payload) is None:
                return out  # 缓存只存校验过的输出，double-check 防手改缓存文件
        except Exception:  # noqa: BLE001 缓存坏了当 miss
            pass
    try:
        # 思考模式与 chat 侧共用一个开关；关掉时 temperature=0 才真正生效
        _mode = _thinking_mode()
        extra = {"thinking": {"type": _mode}} if _mode else {}
        resp = requests.post(
            f"{os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
            json={
                "model": os.environ.get("LLM_MODEL", "deepseek-flash"),
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"{prompt_hint}\n\n输入事实 JSON：\n"
                        + json.dumps(payload, ensure_ascii=False, default=str),
                    },
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                **extra,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # 剥 markdown 围栏（未实现 JSON mode 的 OpenAI 兼容商也能用）
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        out = json.loads(content)["sections"]
    except Exception as e:  # noqa: BLE001 fail-open：任何故障回落规则引擎
        _fallback(module, f"{type(e).__name__}: {e}")
        return None
    if (err := validate_sections(out, payload)) is not None:
        _fallback(module, err)
        return None
    result = {"sections": out}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False))
        _cache_prune(module)
    except OSError:  # noqa: BLE001 缓存写失败不影响本次结果
        pass
    return result
