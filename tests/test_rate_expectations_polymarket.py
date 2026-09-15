"""rates/pricing FOMC 概率 Polymarket 对照：匹配/聚合纯函数测试。

被测对象 src.fed_analysis.polymarket_fomc_odds 及其内部解析
（月份词、跨年、档位聚合、未匹配补位），不读盘。
"""

from __future__ import annotations

from src.fed_analysis import _decision_event_ym, polymarket_fomc_odds


def _q(bucket: str, meeting: str = "September 2026") -> str:
    """按档位生成 Polymarket 决策问题文本。"""
    if bucket == "other":
        return (
            f"Will the Fed decide differently in the next three decisions ({meeting})?"
        )
    if bucket == "hold":
        return (
            "Will there be no change in Fed interest rates "
            f"after the {meeting} meeting?"
        )
    verb = {"cut": "decrease", "hike": "increase"}[bucket.removesuffix("50")]
    bps = "50+" if bucket.endswith("50") else "25"
    return (
        f"Will the Fed {verb} interest rates by {bps} bps after the {meeting} meeting?"
    )


def _event(
    month: str = "september",
    end: str = "2026-09-16",
    markets: list[tuple] | None = None,
    title: str | None = None,
    slug: str | None = None,
) -> dict:
    """构造 fed 决策事件快照。markets 元素为 (bucket, prob_yes[, meeting 文本])。"""
    if markets is None:
        markets = [("cut", 0.0015), ("hold", 0.125), ("hike", 0.875)]
    rows = []
    for t in markets:
        bucket, p, *rest = t  # 可选第三位：meeting 文本（跨年场景用）
        if bucket in {"cut", "cut50", "hold", "hike", "hike50", "other"}:
            question = _q(bucket, rest[0] if rest else "September 2026")
        else:  # 非档位键 → 视为原始问题文本透传（无年份/自定义问题用）
            question = bucket
        rows.append({"id": str(len(rows)), "question": question, "prob_yes": p})
    return {
        "slug": slug or f"fed-decision-in-{month}-762",
        "title": title or f"Fed Decision in {month.capitalize()}?",
        "category": "fed",
        "end_date": end,
        "markets": rows,
    }


class TestPolymarketFomcOdds:
    def test_bucket_aggregation(self) -> None:
        """cut = Σ decrease、hold = no change、hike = Σ increase（含 50+ 档）。"""
        out = polymarket_fomc_odds(
            [
                _event(
                    markets=[
                        ("cut", 0.02),
                        ("cut50", 0.01),
                        ("hold", 0.3),
                        ("hike", 0.6),
                        ("hike50", 0.07),
                    ]
                )
            ],
            ["2026-09-16"],
        )
        assert out == {"2026-09-16": {"cut": 0.03, "hold": 0.3, "hike": 0.67}}

    def test_month_match(self) -> None:
        """September 事件只匹配 9 月会议，10 月会议不串。"""
        out = polymarket_fomc_odds([_event()], ["2026-09-16", "2026-10-28"])
        assert "2026-09-16" in out
        assert "2026-10-28" not in out

    def test_cross_year(self) -> None:
        """December 2026 与 January 2027 同快照并存，按 (年, 月) 精确区分。"""
        dec = _event(
            "december", end="2026-12-09", markets=[("hold", 1.0, "December 2026")]
        )
        jan = _event(
            "january", end="2027-01-27", markets=[("hold", 1.0, "January 2027")]
        )
        out = polymarket_fomc_odds([dec, jan], ["2026-12-09", "2027-01-27"])
        assert out == {
            "2026-12-09": {"cut": 0.0, "hold": 1.0, "hike": 0.0},
            "2027-01-27": {"cut": 0.0, "hold": 1.0, "hike": 0.0},
        }

    def test_year_fallback_to_end_date(self) -> None:
        """问题文本无年份 → 回退事件 end_date 年份（决策事件 end_date 即会议日）。"""
        e = _event(markets=[("Will there be no change in Fed interest rates?", 1.0)])
        assert _decision_event_ym(e) == (2026, 9)  # end_date=2026-09-16
        e["end_date"] = "2027-01-27"
        e["title"] = "Fed Decision in January?"
        out = polymarket_fomc_odds([e], ["2027-01-27", "2026-01-28"])
        assert "2027-01-27" in out
        assert "2026-01-28" not in out

    def test_non_decision_events_ignored(self) -> None:
        """非「Fed Decision in {Month}?」标题的事件不参与（即使问题含档位关键词）。"""
        e = _event(
            markets=[("Will the Fed decrease interest rates in 2026?", 0.5)],
            title="How many Fed rate cuts in 2026?",
            slug="how-many-fed-rate-cuts-in-2026",
        )
        assert polymarket_fomc_odds([e], ["2026-09-16"]) == {}

    def test_unclassifiable_question_skipped(self) -> None:
        """无 decrease/no change/increase 关键词的档位不计入聚合。"""
        e = _event(markets=[("other", 0.9), ("hold", 0.1)])
        out = polymarket_fomc_odds([e], ["2026-09-16"])
        assert out["2026-09-16"] == {"cut": 0.0, "hold": 0.1, "hike": 0.0}

    def test_no_events_returns_empty(self) -> None:
        """快照缺失（events 空/None）→ 空对照，调用方补 null。"""
        assert polymarket_fomc_odds([], ["2026-09-16"]) == {}
        assert polymarket_fomc_odds(None, ["2026-09-16"]) == {}

    def test_bad_meeting_date_skipped(self) -> None:
        """非法会议日期跳过，不抛异常。"""
        out = polymarket_fomc_odds([_event()], ["not-a-date", "2026-09-16"])
        assert list(out) == ["2026-09-16"]
