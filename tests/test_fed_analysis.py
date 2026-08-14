"""fed_analysis 鹰鸽评分引擎单元测试（词表打分 / 分档 / 聚合）。"""

import pandas as pd
import pytest

from src.fed_analysis import (
    _pre_meeting_indicator,
    _score_text,
    fed_analysis,
    stance_label,
)


class TestScoreText:
    def test_hawkish_statement_positive(self):
        # 加息 + 通胀高企 + 强硬语气 → 明显鹰派
        text = (
            "The Committee decided to raise the target range. "
            "Inflation remains elevated. "
            "The Committee is strongly committed to price stability."
        )
        score, hits = _score_text(text)
        assert score >= 3
        assert "raise the target range" in hits
        assert "inflation remains elevated" in hits

    def test_dovish_statement_negative(self):
        text = (
            "The Committee decided to lower the target range. "
            "Inflation has eased. The labor market has cooled."
        )
        score, _ = _score_text(text)
        assert score <= -3

    def test_maintain_statement_neutral(self):
        text = (
            "The Committee decided to maintain the target range. "
            "The economy is expanding."
        )
        score, _ = _score_text(text)
        assert -1 <= score <= 1

    def test_dissent_preferred_to_raise_adds_hawkish_pressure(self):
        text = (
            "The Committee decided to maintain the target range.\n"
            "Voting against the action were Hammack and Logan, who preferred to raise "
            "the target range by 1/4 percentage point at this meeting."
        )
        score, hits = _score_text(text)
        # 反对票段独立计分（+1），不再被通用动作词表重复计分（+4）
        assert score == 1.0
        assert any("dissent" in h for h in hits)
        assert "raise the target range" not in hits

    def test_overlapping_phrases_not_double_counted(self):
        # 'rate cuts' 同时命中其子串 'rate cut' → 只按最长短语计一次（审计 P1-⑫）
        score, hits = _score_text("The committee expects rate cuts later this year.")
        assert score == -3.0
        assert hits.count("rate cuts") == 1
        assert "rate cut" not in hits

    def test_repeated_phrase_counts_once(self):
        # 同一短语多次出现只计一次分（审计 C-1，hits 不重复）
        text = "Rate cuts are likely. More rate cuts would follow if data warrants."
        score, hits = _score_text(text)
        assert score == -3.0
        assert hits.count("rate cuts") == 1

    def test_speech_action_words_downgraded(self):
        # 演讲里讨论降息（如 "markets expect rate cuts"）不应得满分鸽派
        text = "Markets expect rate cuts this year, but the outlook is data dependent."
        full, _ = _score_text(text, action_weight=1.0)
        half, _ = _score_text(text, action_weight=0.5)
        assert half > full  # 演讲权重减半 → 评分更温和（负分幅度更小）

    def test_score_clamped_to_5(self):
        text = (
            "The Committee decided to raise the target range. "
            "Inflation remains elevated. Strongly committed to price stability. "
            "The Committee is vigilant."
        )
        score, _ = _score_text(text)
        assert 0 < score <= 5.0


class TestStanceLabel:
    def test_buckets(self):
        assert stance_label(5.0) == "极鹰"
        assert stance_label(3.0) == "鹰派"
        assert stance_label(1.0) == "偏鹰"
        assert stance_label(0.0) == "中性"
        assert stance_label(-1.0) == "偏鸽"
        assert stance_label(-3.0) == "鸽派"
        assert stance_label(-5.0) == "极鸽"


class TestFedAnalysis:
    @pytest.fixture
    def fake_fed_data(self, tmp_path, monkeypatch):
        """tmp_path 下伪造声明/演讲 CSV，聚合逻辑不依赖真实数据。"""
        import src.fed_analysis as fa

        st = pd.DataFrame(
            [
                {
                    "id": "monetary20260729a",
                    "date": "20260729",
                    "kind": "statement",
                    "title": "Federal Reserve issues FOMC statement",
                    "url": "https://x/monetary20260729a.htm",
                    "body": "The Committee decided to maintain the target range. "
                    "Inflation remains elevated.",
                }
            ]
        )
        sp = pd.DataFrame(
            [
                {
                    "id": "waller20260713a",
                    "date": "20260713",
                    "kind": "speech",
                    "speaker": "Waller",
                    "title": "Governor Waller on the economic outlook",
                    "url": "https://x/waller20260713a.htm",
                    "body": "I support keeping policy restrictive. "
                    "Inflation remains elevated.",
                },
                {
                    "id": "cook20260715a",
                    "date": "20260715",
                    "kind": "speech",
                    "speaker": "Cook",
                    "title": "Governor Cook on the economic outlook",
                    "url": "https://x/cook20260715a.htm",
                    "body": "Inflation has eased and the labor market has cooled.",
                },
            ]
        )
        st_csv, sp_csv = tmp_path / "statements.csv", tmp_path / "speeches.csv"
        st.to_csv(st_csv, index=False)
        sp.to_csv(sp_csv, index=False)
        monkeypatch.setattr(fa, "STATEMENTS_CSV", st_csv)
        monkeypatch.setattr(fa, "SPEECHES_CSV", sp_csv)

    def test_aggregation(self, fake_fed_data):
        out = fed_analysis()
        assert out["statements"][0]["kind"] == "statement"
        assert out["statements"][0]["score"] > 0
        # Cook 鸽派演讲 → 负分；Waller 鹰派 → 正分
        by_speaker = {s["speaker"]: s for s in out["speeches"]}
        assert by_speaker["Cook"]["score"] < 0
        assert by_speaker["Waller"]["score"] > 0
        # 立场表：每人一条；时间线含声明+演讲
        assert {s["speaker"] for s in out["stances"]} == {"Cook", "Waller"}
        assert len(out["timeline"]) == 3
        assert out["indicator"]["sample"] == 3

    def test_roster_lists_all_voting_members(self, fake_fed_data):
        """官方 12 人名单全在，无演讲的成员标中性/暂无。"""
        out = fed_analysis()
        assert len(out["roster"]) == 12
        assert all(r["votes"] for r in out["roster"])
        waller = next(r for r in out["roster"] if r["speaker"] == "Waller")
        assert waller["score"] > 0 and waller["date"] == "20260713"
        goolsbee = next(r for r in out["roster"] if r["speaker"] == "Goolsbee")
        assert goolsbee["date"] is None and goolsbee["label"] == "中性"

    def test_missing_data_returns_error(self, tmp_path, monkeypatch):
        import src.fed_analysis as fa

        monkeypatch.setattr(fa, "STATEMENTS_CSV", tmp_path / "nope.csv")
        monkeypatch.setattr(fa, "SPEECHES_CSV", tmp_path / "nope2.csv")
        out = fed_analysis()
        assert "error" in out


class TestPreMeetingIndicator:
    def test_window_filters_speeches_before_last_fomc(self):
        """固定 today=2026-07-29：窗口 07-15..07-29，窗口外/空数据不计数。"""
        df = pd.DataFrame(
            {
                "date": ["20260720", "20260726", "20260601", "20260801"],
                "score": ["2.0", "3.0", "-5.0", "4.0"],
            }
        )
        pm = _pre_meeting_indicator(df, today=pd.Timestamp("2026-07-29"))
        assert pm["meeting"] == "2026-07-29"
        assert pm["sample"] == 2  # 仅 07-20/07-26；会前更早与会议后都排除
        assert pm["score"] == 2.5
        assert pm["label"] == "鹰派"

    def test_empty_or_no_window_returns_none(self):
        df = pd.DataFrame({"date": ["20250101"], "score": ["1.0"]})
        assert _pre_meeting_indicator(df, today=pd.Timestamp("2026-07-29")) is None
        assert (
            _pre_meeting_indicator(pd.DataFrame(), today=pd.Timestamp("2026-07-29"))
            is None
        )
