"""fed_fetcher 单元测试：标题清理 / 声明分类 / 演讲人提取 / 正文提取。"""

from src.fetchers.fed_fetcher import (
    _clean_title,
    _extract_body,
    _speaker_of,
    _statement_kind,
)


class TestCleanTitle:
    def test_strips_board_prefix(self):
        assert _clean_title("Federal Reserve Board - Federal Reserve issues FOMC statement") == (
            "Federal Reserve issues FOMC statement"
        )

    def test_strips_speech_prefix(self):
        assert _clean_title("Speech by Governor Waller on the economic outlook") == (
            "Governor Waller on the economic outlook"
        )

    def test_strips_trailing_suffix(self):
        assert _clean_title("Governor Cook on the economic outlook - Federal Reserve Board") == (
            "Governor Cook on the economic outlook"
        )

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


# ── 票委名册抓取（fetch_roster）─────────────────────────────────────────────
# 这一段是 review 补的：原改动只有手工验证，没有单测锁住解析与告警。

import logging  # noqa: E402

import pytest  # noqa: E402

import src.fetchers.fed_fetcher as ff  # noqa: E402


def _fomc_page(
    voters: list[tuple[str, str, str]], alternates: list[tuple[str, str]], *, anchor: bool = True
) -> str:
    """拼一份最小 fomc.htm 文本（走 _TAG_RE 之前的 HTML，含 <br />）。"""
    v = "".join(f"<br />{n}, {p}{', ' + t if t else ''}" for n, p, t in voters)
    a = "".join(f"<br />{n}, {p}" for n, p in alternates)
    rot = "Federal Reserve Bank Rotation on the FOMC" if anchor else "SOMETHING_ELSE"
    # 每个 <div> 之间留换行：真实页面 strip 标签后靠 \n 分隔行，
    # 不留换行会让最后一行候补与下一段粘连、解析不出（fixture 踩过这个坑）。
    return (
        f"<div>2026 Committee Members{v}\n</div>"
        f"<div>Alternate Members{a}\n</div>"
        f"<div>{rot} 2027 New York Chicago</div>"
    )


VOTERS_12 = [
    ("Kevin Warsh", "Board of Governors", "Chairman"),
    ("John C. Williams", "New York", "Vice Chair"),
    ("Michael S. Barr", "Board of Governors", ""),
    ("Michelle W. Bowman", "Board of Governors", ""),
    ("Lisa D. Cook", "Board of Governors", ""),
    ("Beth M. Hammack", "Cleveland", ""),
    ("Philip N. Jefferson", "Board of Governors", ""),
    ("Neel Kashkari", "Minneapolis", ""),
    ("Lorie K. Logan", "Dallas", ""),
    ("Anna Paulson", "Philadelphia", ""),
    ("Jerome H. Powell", "Board of Governors", ""),
    ("Christopher J. Waller", "Board of Governors", ""),
]
ALTS = [("Thomas I. Barkin", "Richmond"), ("Mary C. Daly", "San Francisco")]


def _patch(monkeypatch, tmp_path, page: str):
    monkeypatch.setattr(ff, "_get", lambda url: page)
    monkeypatch.setattr(ff, "ROSTER_CSV", tmp_path / "roster.csv")
    monkeypatch.setattr(ff, "OUT_DIR", tmp_path)


class TestFetchRoster:
    def test_parses_voters_and_alternates(self, tmp_path, monkeypatch):
        """12 票委 + 候补：votes 有真实区分，中文名与城市名都对。"""
        _patch(monkeypatch, tmp_path, _fomc_page(VOTERS_12, ALTS))
        assert ff.fetch_roster() == (14, 0)

        import pandas as pd

        df = pd.read_csv(tmp_path / "roster.csv")
        assert len(df) == 14
        assert df["votes"].sum() == 12  # 候补 votes=False
        chair = df[df["role"] == "美联储主席"].iloc[0]
        assert chair["speaker"] == "Warsh" and chair["name_zh"] == "沃什"
        # 姓氏取最后一个词（"John C. Williams" → Williams）
        assert "Williams" in set(df["speaker"])
        # 城市翻译
        assert "克利夫兰联储主席" in set(df["role"])

    def test_vacancy_11_does_not_raise(self, tmp_path, monkeypatch):
        """理事空缺期官网只列 11 人 → 必须照常写入，不能每天炸掉 fetch_fed。"""
        _patch(monkeypatch, tmp_path, _fomc_page(VOTERS_12[:6] + VOTERS_12[7:], ALTS))
        assert ff.fetch_roster() == (13, 0)

    def test_too_few_voters_raises_and_keeps_old_file(self, tmp_path, monkeypatch):
        """解析退化（<10）→ 抛错且不覆盖旧文件。"""
        csv = tmp_path / "roster.csv"
        csv.write_text("sentinel")
        _patch(monkeypatch, tmp_path, _fomc_page(VOTERS_12[:4], ALTS))
        with pytest.raises(RuntimeError, match="合理区间"):
            ff.fetch_roster()
        assert csv.read_text() == "sentinel"

    def test_duplicate_surnames_raises(self, tmp_path, monkeypatch):
        """解析错位导致重名 → 抛错（防止把轮值表误抓成票委）。"""
        dup = VOTERS_12[:11] + [("Anna Paulson", "Dallas", "")]
        _patch(monkeypatch, tmp_path, _fomc_page(dup, ALTS))
        with pytest.raises(RuntimeError, match="重复姓氏"):
            ff.fetch_roster()

    def test_missing_chairman_raises(self, tmp_path, monkeypatch):
        """Chairman 标记丢了 → 抛错（否则主席会退化成普通理事）。"""
        no_chair = [("Kevin Warsh", "Board of Governors", "")] + VOTERS_12[1:]
        _patch(monkeypatch, tmp_path, _fomc_page(no_chair, ALTS))
        with pytest.raises(RuntimeError, match="美联储主席"):
            ff.fetch_roster()

    def test_missing_rotation_anchor_falls_back(self, tmp_path, monkeypatch, caplog):
        """段末锚点缺失 → 退固定窗口 + 打 warning，仍能正确解析。

        锁住 `alt_end < 0` 这个判断：写成 `not alt_end` 时 -1 是 truthy，
        warning 永远不响（这个 bug 真实发生过一次）。
        """
        _patch(monkeypatch, tmp_path, _fomc_page(VOTERS_12, ALTS, anchor=False))
        with caplog.at_level(logging.WARNING, logger="src.fetchers.fed_fetcher"):
            assert ff.fetch_roster() == (14, 0)
        assert any("Rotation" in r.getMessage() for r in caplog.records)

    def test_missing_members_section_raises(self, tmp_path, monkeypatch):
        """整个「YYYY Committee Members」段都没了 → 抛错。"""
        _patch(monkeypatch, tmp_path, "<div>totally different page</div>")
        with pytest.raises(RuntimeError, match="Committee Members"):
            ff.fetch_roster()
