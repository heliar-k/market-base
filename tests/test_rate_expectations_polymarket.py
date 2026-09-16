"""rates/pricing FOMC 概率 Polymarket 对照：匹配/聚合纯函数测试。

被测对象 src.fed_analysis 的 polymarket_fomc_odds / polymarket_fomc_history /
zq_buckets_from_probs 及其内部解析（月份词、跨年、档位聚合、未匹配补位），不读盘。
"""

from __future__ import annotations

from src.fed_analysis import (
    _decision_event_ym,
    polymarket_fomc_odds,
    zq_buckets_from_probs,
)


def _three(out: dict) -> dict:
    """剥掉新增的 buckets/市场深度字段，只留三档聚合供断言。"""
    return {d: {k: v[k] for k in ("cut", "hold", "hike")} for d, v in out.items()}


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
        assert _three(out) == {"2026-09-16": {"cut": 0.03, "hold": 0.3, "hike": 0.67}}
        # 五档明细：25/50 分档，hold 共用
        assert out["2026-09-16"]["buckets"] == {
            "cut50": 0.01,
            "cut25": 0.02,
            "hold": 0.3,
            "hike25": 0.6,
            "hike50": 0.07,
        }

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
        assert _three(out) == {
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
        assert out["2026-09-16"]["cut"] == 0.0
        assert out["2026-09-16"]["hold"] == 0.1
        assert out["2026-09-16"]["hike"] == 0.0

    def test_no_events_returns_empty(self) -> None:
        """快照缺失（events 空/None）→ 空对照，调用方补 null。"""
        assert polymarket_fomc_odds([], ["2026-09-16"]) == {}
        assert polymarket_fomc_odds(None, ["2026-09-16"]) == {}

    def test_bad_meeting_date_skipped(self) -> None:
        """非法会议日期跳过，不抛异常。"""
        out = polymarket_fomc_odds([_event()], ["not-a-date", "2026-09-16"])
        assert list(out) == ["2026-09-16"]


class TestZqBuckets:
    """ZQ range 概率 → 五档（相对当前目标区间 3.50–3.75）。"""

    T = (3.5, 3.75)

    def test_hold_hike25(self) -> None:
        """跨目标区间→hold；[3.75,4.0] 距上沿 0 步→hike25。"""
        probs = [
            {"lo": 3.5, "hi": 3.75, "prob": 0.0356},
            {"lo": 3.75, "hi": 4.0, "prob": 0.9644},
        ]
        assert zq_buckets_from_probs(probs, *self.T) == {
            "cut50": 0.0,
            "cut25": 0.0,
            "hold": 0.0356,
            "hike25": 0.9644,
            "hike50": 0.0,
        }

    def test_steps_and_cap(self) -> None:
        """距边界 1 步→25 档，≥2 步归 50 档（更远不另分档）；cut 对称。"""
        probs = [
            {"lo": 3.75, "hi": 4.0, "prob": 0.6},
            {"lo": 4.0, "hi": 4.25, "prob": 0.3},
            {"lo": 4.25, "hi": 4.5, "prob": 0.1},
            {"lo": 3.25, "hi": 3.5, "prob": 0.2},
            {"lo": 3.0, "hi": 3.25, "prob": 0.8},
        ]
        out = zq_buckets_from_probs(probs, *self.T)
        assert out == {
            "cut50": 0.8,
            "cut25": 0.2,
            "hold": 0.0,
            "hike25": 0.6,
            "hike50": 0.4,
        }

    def test_no_target_returns_none(self) -> None:
        assert zq_buckets_from_probs([], None, 3.75) is None


class TestPolymarketFomcHistory:
    """polymarket_fomc_history：日频五档→三档聚合（monkeypatch，不读盘）。"""

    def test_five_to_three_by_date(self, monkeypatch) -> None:
        """五档 market 按日聚合为 cut/hold/hike，日期升序。"""
        import src.fed_analysis as fa

        # _event 按传入顺序给 market 编 id "0".."4" → cut25/cut50/hold/hike25/hike50
        e = _event(
            markets=[
                ("cut", 0),
                ("cut50", 0),
                ("hold", 0),
                ("hike", 0),
                ("hike50", 0),
            ]
        )
        history = {
            "0": [
                {"date": "2026-09-15", "value": 0.02},
                {"date": "2026-09-16", "value": 0.01},
            ],
            "1": [
                {"date": "2026-09-15", "value": 0.01},
                {"date": "2026-09-16", "value": 0.03},
            ],
            "2": [
                {"date": "2026-09-15", "value": 0.30},
                {"date": "2026-09-16", "value": 0.36},
            ],
            "3": [
                {"date": "2026-09-15", "value": 0.60},
                {"date": "2026-09-16", "value": 0.55},
            ],
            "4": [
                {"date": "2026-09-15", "value": 0.07},
                {"date": "2026-09-16", "value": 0.05},
            ],
        }
        monkeypatch.setattr(
            fa,
            "market_odds",
            lambda: {"as_of": "2026-09-16", "events": [e], "history": history},
        )
        out = fa.polymarket_fomc_history()
        assert out["as_of"] == "2026-09-16"
        assert out["meetings"]["2026-09"] == [
            {"date": "2026-09-15", "cut": 0.03, "hold": 0.3, "hike": 0.67},
            {"date": "2026-09-16", "cut": 0.04, "hold": 0.36, "hike": 0.6},
        ]

    def test_no_data_returns_empty(self, monkeypatch) -> None:
        """market_odds 无数据（None）→ 空 meetings，不抛异常。"""
        import src.fed_analysis as fa

        monkeypatch.setattr(fa, "market_odds", lambda: None)
        assert fa.polymarket_fomc_history() == {"as_of": None, "meetings": {}}
