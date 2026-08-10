"""Refunding fetcher 单元测试：页面解析 + 正文提取 + 增量去重。"""

from src.fetchers.refunding_fetcher import (
    _doc_kind,
    _extract_body,
    _extract_date,
    _quarter_of,
    fetch_refunding,
)

# 最新季度文档页片段：两个目标链接 + 一个干扰链接
_INDEX_HTML = """
<a href="/news/press-releases/sb0590">Policy Statement: 2026 - 3rd Quarter</a>
<a href="/news/press-releases/sb0584">Financing Estimates: 2026 - 3rd Quarter</a>
<a href="/news/press-releases/sb0585">Economic Policy Statements to TBAC:
2026 - 3rd Quarter</a>
"""

# 新闻稿页片段：日期 + 正文容器
_BODY_TEXT = (
    "WASHINGTON — The U.S. Department of the Treasury is offering $125 billion "
    "of Treasury securities. The balance of Treasury financing requirements "
    "will be met with regular weekly bill auctions."
)
_RELEASE_HTML = f"""
<div class="date-format field field--name-field-news-publication-date
    field--type-datetime field--label-hidden field__item"><time
    datetime="2026-08-05T12:30:00Z" class="datetime">August 5, 2026</time></div>
<div class="clearfix text-formatted field field--name-field-news-body
    field--type-text-long field--label-hidden field__item"><p>{_BODY_TEXT}</p>
    <p class="text-align-center">###</p></div>
<div class="field field--name-field-news-use-featured-image
    field--type-boolean field--label-above"></div>
"""


class _FakeResp:
    def __init__(self, url: str):
        self.url = url
        self.text = _RELEASE_HTML if "press-releases" in url else _INDEX_HTML

    @staticmethod
    def raise_for_status():
        pass


def _fake_get(*a, **k):
    url = a[0] if a else k.get("url", "")
    return _FakeResp(url)


def test_doc_kind_and_quarter():
    assert _doc_kind("Policy Statement: 2026 - 3rd Quarter") == "statement"
    assert _doc_kind("Financing Estimates: 2025 - 4th Quarter") == "financing_estimates"
    assert _doc_kind("Economic Policy Statements to TBAC: 2026 - 3rd Quarter") == ""
    assert _quarter_of("Policy Statement: 2026 - 3rd Quarter") == "2026-Q3"
    assert _quarter_of("Financing Estimates: 2025 - 4th Quarter") == "2025-Q4"
    assert _quarter_of("unrelated") == ""


def test_extract_date_and_body():
    assert _extract_date(_RELEASE_HTML) == "2026-08-05"
    body = _extract_body(_RELEASE_HTML)
    assert "WASHINGTON" in body
    assert "weekly bill auctions" in body
    assert "<p>" not in body  # 标签已剥离


def test_fetch_refunding_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr("src.fetchers.refunding_fetcher._SESSION", _FakeSession())
    monkeypatch.setattr(
        "src.fetchers.refunding_fetcher.OUT_CSV", tmp_path / "refunding.csv"
    )

    new, skipped = fetch_refunding()
    assert new == 2
    assert skipped == 0

    # 第二次运行：全部跳过
    new, skipped = fetch_refunding()
    assert new == 0
    assert skipped == 2

    import pandas as pd

    df = pd.read_csv(tmp_path / "refunding.csv", dtype=str)
    assert len(df) == 2
    assert set(df["kind"]) == {"statement", "financing_estimates"}
    assert set(df["quarter"]) == {"2026-Q3"}
    assert all(df["date"] == "2026-08-05")
    assert df["body"].iloc[0].startswith("WASHINGTON")


class _FakeSession:
    """模拟 Session.get 返回按 URL 分派的假响应。"""

    @staticmethod
    def get(url: str, timeout: int = 20) -> _FakeResp:
        return _FakeResp(url)
