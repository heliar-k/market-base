"""fed_fetcher 单元测试：标题清理 / 声明分类 / 演讲人提取 / 正文提取。"""

from src.fetchers.fed_fetcher import (
    _clean_title,
    _extract_body,
    _speaker_of,
    _statement_kind,
)


class TestCleanTitle:
    def test_strips_board_prefix(self):
        assert _clean_title(
            "Federal Reserve Board - Federal Reserve issues FOMC statement"
        ) == ("Federal Reserve issues FOMC statement")

    def test_strips_speech_prefix(self):
        assert _clean_title("Speech by Governor Waller on the economic outlook") == (
            "Governor Waller on the economic outlook"
        )

    def test_strips_trailing_suffix(self):
        assert _clean_title(
            "Governor Cook on the economic outlook - Federal Reserve Board"
        ) == ("Governor Cook on the economic outlook")

    def test_plain_title_untouched(self):
        assert _clean_title("Minutes of the Federal Open Market Committee") == (
            "Minutes of the Federal Open Market Committee"
        )


class TestStatementKind:
    def test_fomc_statement(self):
        assert _statement_kind("Federal Reserve issues FOMC statement") == "statement"

    def test_minutes(self):
        assert _statement_kind(
            "Minutes of the Federal Open Market Committee, June 16-17, 2026"
        ) == ("minutes")

    def test_sep(self):
        assert (
            _statement_kind(
                "Federal Reserve Board and Federal Open Market Committee "
                "release economic projections"
            )
            == "sep"
        )

    def test_discount_rate_minutes(self):
        assert _statement_kind(
            "Minutes of the Board's discount rate meetings on June 8 and June 17"
        ) == ("discount")

    def test_other(self):
        assert _statement_kind(
            "Federal Open Market Committee reaffirms its Statement on Longer-Run Goals"
        ) == ("other")


class TestSpeakerOf:
    def test_from_doc_id(self):
        assert _speaker_of("jefferson20260716a") == "Jefferson"

    def test_empty_for_non_speech_id(self):
        # 非“姓氏+日期”形态的 id 不提取
        assert _speaker_of("abc") == ""


class TestExtractBody:
    _HTML = """
<html><head><title>X</title></head><body>
<div id="article">
  <h3>Title</h3>
  <p>The Committee decided to <b>maintain</b> the target range.</p>
  <script>var junk = 1;</script>
</div>
<div class="row footer">footer junk</div>
<div class="col-xs-12 col-sm-4">sidebar junk</div>
</body></html>
"""

    def test_extracts_main_column_text(self):
        body = _extract_body(self._HTML)
        assert "The Committee decided to maintain the target range." in body
        assert "sidebar junk" not in body
        assert "var junk" not in body

    def test_extract_body_falls_back_to_full_html(self):
        assert _extract_body("<p>no container</p>") == "no container"


class TestFetchItems:
    _HTML = (
        "<html><title>FRB: X</title><body>"
        '<div id="article"><h3>Minutes of the Federal Open Market Committee</h3>'
        "<p>body text</p></div></body></html>"
    )

    def test_fixed_column_and_article_title(self, tmp_path, monkeypatch):
        """fixed 列（kind=minutes）写入；标题取自 #article 内 h3 而非 <title>。"""
        import pandas as pd

        from src.fetchers import fed_fetcher as ff

        monkeypatch.setattr(ff, "_get", lambda url: self._HTML)
        out = tmp_path / "st.csv"
        new, _ = ff._fetch_items(
            {"fomcminutes20260128": "http://x/fomcminutes20260128.htm"},
            out,
            ["id", "date", "title", "url", "body"],
            {"kind": "minutes"},
        )
        assert new == 1
        df = pd.read_csv(out)
        assert df.iloc[0]["kind"] == "minutes"
        assert df.iloc[0]["title"] == "Minutes of the Federal Open Market Committee"

    def test_backfill_recomputes_existing_column(self, tmp_path):
        """派生列无条件重算：修复新追加行 speaker 为空的 bug（如 waller20260903）。"""
        import pandas as pd

        from src.fetchers import fed_fetcher as ff

        out = tmp_path / "sp.csv"
        pd.DataFrame(
            [
                {
                    "id": "waller20260903a",
                    "date": "",
                    "title": "The Economic Outlook",
                    "speaker": "",
                    "url": "u",
                    "body": "b",
                },
                {
                    "id": "fomcminutes20260128",
                    "date": "",
                    "title": "Minutes of the Federal Open Market Committee",
                    "speaker": "",
                    "url": "v",
                    "body": "b",
                },
            ]
        ).to_csv(out, index=False)
        ff._backfill(out, {"speaker": ("id", ff._speaker_of)})
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["speaker"] == "Waller"  # 旧逻辑下会保持空串
        assert df.iloc[0]["date"] == "20260903"  # 日期列正常回填

        # 纪要 fixed kind 与标题重算结果一致：_fetch_items 后再 _backfill 不翻转
        st = tmp_path / "st.csv"
        pd.DataFrame(
            [
                {
                    "id": "fomcminutes20260128",
                    "date": "",
                    "title": "Minutes of the Federal Open Market Committee",
                    "kind": "minutes",
                    "url": "u",
                    "body": "b",
                }
            ]
        ).to_csv(st, index=False)
        ff._backfill(st, {"kind": ("title", ff._statement_kind)})
        assert pd.read_csv(st, dtype=str).iloc[0]["kind"] == "minutes"
