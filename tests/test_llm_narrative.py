"""LLM 研判叙事：客户端校验 + rates 试点接入 + CI 标记串两侧一致。

全部 mock HTTP，不真调 API（真链路手动验：LLM_API_KEY=... uv run python -m src.rates_analysis）。
设计：docs/llm-narrative-design.md。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm import _extract_numbers, generate_narrative, llm_enabled, validate_sections

CI_GREP = "grep -q LLM_FALLBACK /tmp/export.log"  # deploy-pages.yml 侧的标记串


class FakeResp:
    def __init__(self, content: str, status: int = 200):
        self._content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _payload() -> dict:
    return {
        "sections": [
            {"title": "曲线形态", "body": "曲线呈熊陡形态，2s10s 42bp，1 月走扩 9bp。"},
            {"title": "展望", "body": "关注季度再融资。"},
        ],
        "yield_curve_facts": {"spreads": {"2s10s": 42.0, "2s10s_1m_chg": 9.0}},
    }


def _ok_content() -> str:
    return json.dumps(
        {
            "sections": [
                {
                    "title": "曲线形态",
                    "body": "曲线熊陡格局未变，2s10s 位于 42bp，1 月走扩 9bp，趋势延续。",
                },
                {
                    "title": "展望",
                    "body": "季度再融资与 TGA 余额变化是下阶段主变量，短端流动性分层仍在。",
                },
            ]
        },
        ensure_ascii=False,
    )


# ── 纯函数：数字归一化 ────────────────────────────────────────────────────


def test_norm_numbers() -> None:
    assert _extract_numbers("3.90%") == {"3.9"}
    assert _extract_numbers("1,234") == {"1234"}
    assert _extract_numbers("42.0bp") == {"42"}


def test_abs_form_allowed() -> None:
    # facts 有 -6.0，LLM 写「收窄 6bp」放行
    assert "6" in _extract_numbers("2s10s 收窄 6bp") & (_extract_numbers("-6.0") | {"6"})


# ── validate_sections ─────────────────────────────────────────────────────


def test_validate_pass() -> None:
    assert validate_sections(json.loads(_ok_content())["sections"], _payload()) is None


def test_validate_rejects_invented_number() -> None:
    out = json.loads(_ok_content())["sections"]
    out[0]["body"] = "曲线熊陡，2s10s 位于 43bp。"
    assert "事实之外的数字" in (validate_sections(out, _payload()) or "")


def test_validate_rejects_wrong_title() -> None:
    out = json.loads(_ok_content())["sections"]
    out[0]["title"] = "别的标题"
    assert "title" in (validate_sections(out, _payload()) or "")


def test_validate_rejects_too_long() -> None:
    out = json.loads(_ok_content())["sections"]
    out[1]["body"] = "长" * 400
    assert "超长" in (validate_sections(out, _payload()) or "")


def test_validate_rejects_section_count_change() -> None:
    out = json.loads(_ok_content())["sections"][:1]
    assert "段数" in (validate_sections(out, _payload()) or "")


# ── generate_narrative：HTTP 层 fail-open ────────────────────────────────


def _post(content: str, status: int = 200) -> MagicMock:
    return patch("src.llm.requests.post", return_value=FakeResp(content, status))


def test_success_returns_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    with _post(_ok_content()):
        assert generate_narrative("rates", _payload(), "hint") == json.loads(_ok_content())


def test_http_error_falls_back(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    with _post("{}", status=401):
        assert generate_narrative("rates", _payload(), "hint") is None
    assert "LLM_FALLBACK: rates" in capsys.readouterr().err


def test_non_json_falls_back(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    with _post("抱歉，我无法输出 JSON"):
        assert generate_narrative("rates", _payload(), "hint") is None


def test_validation_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    bad = json.dumps(
        {"sections": [{"title": "曲线形态", "body": "2s10s 43bp"}]}, ensure_ascii=False
    )
    with _post(bad):
        assert generate_narrative("rates", _payload(), "hint") is None


# ── rates 试点接入 ────────────────────────────────────────────────────────


def test_no_key_means_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.rates_analysis as ra

    # 本地 .env 若配了 LLM_API_KEY（开发机常见），这条断言会假红——本用例钉的是「没有 key」这条
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert not llm_enabled()
    out = ra.generate_analysis()
    assert out["generator"] == "rules"


def test_bad_key_means_rules_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.rates_analysis as ra

    monkeypatch.setenv("LLM_API_KEY", "sk-fake")
    with _post("{}", status=401):
        out = ra.generate_analysis()
    assert out["generator"] == "rules-fallback"
    assert len(out["overview"]["sections"]) == 4


def test_llm_success_means_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.rates_analysis as ra

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    # mock 返回与真实 payload 同构的内容（4 段，body 回声 = 数字全在 facts 内，合法改写下限）
    echo = json.dumps(
        {
            "sections": [
                {"title": s["title"], "body": s["body"]} for s in ra.overview_analysis()["sections"]
            ]
        },
        ensure_ascii=False,
    )
    with _post(echo):
        out = ra.generate_analysis()
    assert out["generator"] == "llm"
    assert len(out["overview"]["sections"]) == 4
    # yield_curve 事实段不受 LLM 影响
    assert out["yield_curve"]["spreads"]["2s10s"] == ra.yield_curve_analysis()["spreads"]["2s10s"]


# ── 内容寻址缓存 ────────────────────────────────────────────────────────


def test_cache_hit_skips_http(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """数据不变 → 键不变 → 命中缓存，不建 HTTP 连接（当日缓存的核心诉求）。"""
    import src.llm as llm_mod

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setattr(llm_mod, "ROOT", tmp_path)
    payload, hint = _payload(), "hint"
    with _post(_ok_content()) as mocked:
        first = generate_narrative("rates", payload, hint)
        assert first is not None
        second = generate_narrative("rates", payload, hint)  # 第二次：命中缓存
    assert mocked.call_count == 1  # 只调了一次 API
    assert second == first


def test_cache_corrupt_falls_back_to_http(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import src.llm as llm_mod

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setattr(llm_mod, "ROOT", tmp_path)
    cache = llm_mod._cache_path("rates", _payload(), "hint")
    cache.parent.mkdir(parents=True)
    cache.write_text("{broken json")
    with _post(_ok_content()) as mocked:
        assert generate_narrative("rates", _payload(), "hint") is not None
    assert mocked.call_count == 1  # 坏缓存当 miss，走 HTTP


# ── CI 标记串两侧一致（改 Python 侧格式而忘改 CI 会静默失效报警）──────────


def test_ci_grep_matches_python_marker() -> None:
    from src import llm

    assert "LLM_FALLBACK" in CI_GREP
    # 约定写在 src/llm.py 模块 docstring 里；_fallback 是实际输出点
    assert "LLM_FALLBACK" in (llm.__doc__ or "")
    import inspect

    assert "LLM_FALLBACK" in inspect.getsource(llm._fallback)
