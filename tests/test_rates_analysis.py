"""rates_analysis 规则引擎单元测试（形态判定 / 失效条件 / 盈亏平衡 / 交易目标）。"""

import pandas as pd
import pytest

import src.rates_analysis as ra
from src.rates_analysis import (
    _breakeven,
    _coupon_cover,
    _curve_text,
    _driver_label,
    _fed_expectation_text,
    _invalidation,
    _shape_label,
    _spread_vs_us,
    _time_frame,
    _trade_implications,
    yield_curve_analysis,
)


class TestShapeLabel:
    def test_bear_steepening(self):
        # 2s10s 走扩 +10bp 且 10Y 上行 → 熊陡
        assert _shape_label(10.0, 15.0) == "熊陡"

    def test_bull_steepening(self):
        # 2s10s 走扩 +10bp 但 10Y 下行 → 牛陡
        assert _shape_label(10.0, -15.0) == "牛陡"

    def test_bear_flattening(self):
        # 2s10s 收窄 -10bp 且 10Y 上行 → 熊平
        assert _shape_label(-10.0, 15.0) == "熊平"

    def test_bull_flattening(self):
        assert _shape_label(-10.0, -15.0) == "牛平"

    def test_flat_within_8bp(self):
        assert _shape_label(5.0, 3.0) == "走平"

    def test_missing_data(self):
        assert _shape_label(None, None) == "数据不足"


class TestInvalidation:
    def test_steepening_fails_on_narrowing(self):
        # 熊陡（正利差）：失效 = 收窄 20bp 至 25bp 以下
        conds = _invalidation("熊陡", 45.0, 4.68)
        assert "收窄至 25bp 以下" in conds[0]

    def test_inverted_curve_narrowing_means_deeper_inversion(self):
        # 牛陡（倒挂 -20bp）：失效 = 利差收窄（更倒挂），不能输出 "+" 方向的数值
        conds = _invalidation("牛陡", -20.0, 4.0)
        assert "收窄至 -40bp 以下" in conds[0]

    def test_flattening_fails_on_widening(self):
        conds = _invalidation("熊平", 45.0, 4.68)
        assert "走扩至 65bp 以上" in conds[0]

    def test_flat_needs_directional_move(self):
        conds = _invalidation("走平", 45.0, 4.68)
        assert "单方向移动超 20bp" in conds[0]


class TestSpreadVsUs:
    def test_us_minus_local_sign(self):
        # 符号约定：美国 − 该市场（对齐 timsun，审计 P1-④）
        assert _spread_vs_us(2.67, 4.75) == 208.0

    def test_missing_side_returns_none(self):
        # NaN 全列/缺失不得伪造 0 利差（审计 P1-⑤）
        assert _spread_vs_us(None, 4.75) is None
        assert _spread_vs_us(2.67, None) is None


class TestBreakeven:
    def test_normal(self):
        assert _breakeven(4.68, 2.41) == 2.27

    def test_missing_side_returns_none(self):
        # 缺失一侧不得伪造 0.00%
        assert _breakeven(4.68, None) is None
        assert _breakeven(None, 2.41) is None


class TestTradeImplications:
    def test_steepening_has_target_stop(self):
        trades = _trade_implications("熊陡", 45.0)
        assert any(
            "走扩至 60bp 为目标" in t and "收窄至 30bp 以下止损" in t for t in trades
        )

    def test_flat_has_no_target(self):
        trades = _trade_implications("走平", 45.0)
        assert not any("目标/止损" in t for t in trades)


class TestTimeFrame:
    def test_fast_move_shortens_window(self):
        assert _time_frame("熊陡", 18.0) == "未来 15-20 个交易日"

    def test_slow_move_default(self):
        assert _time_frame("熊陡", 5.0) == "未来 30 个交易日"


class TestCouponCover:
    def test_averages_last_10_coupon_auctions(self):
        # 11 场付息券（倍数 1.0~3.0）+ 1 场 Bill → 只算付息券，且只取最近 10 场
        rows = []
        for i in range(11):
            rows.append(
                {
                    "auction_date": f"2026-06-{i + 1:02d}",
                    "security_type": "Note",
                    "bid_to_cover_ratio": str(i + 1),
                }
            )
        rows.append(
            {
                "auction_date": "2026-06-12",
                "security_type": "Bill",
                "bid_to_cover_ratio": "9.9",
            }
        )
        auc = pd.DataFrame(rows).set_index("auction_date")
        # 后 10 场均值
        assert _coupon_cover(auc) == pytest.approx(sum(range(2, 12)) / 10)

    def test_empty_returns_none(self):
        assert _coupon_cover(pd.DataFrame()) is None


class TestCurveText:
    """形态叙事的方向必须与形态一致（修复：原模板熊平时仍写「陡峭化延续」）。"""

    def test_flattening_points_to_narrowing(self):
        t = _curve_text("熊平", 36.0, 11.0, 5.17, "短端政策预期")
        assert "收窄至 21bp 以下" in t and "平坦化大概率延续" in t
        assert "陡峭化" not in t

    def test_steepening_points_to_breakout(self):
        t = _curve_text("熊陡", 36.0, 11.0, 5.17, "长端风险溢价重定价")
        assert "突破 51bp" in t and "陡峭化大概率延续" in t

    def test_flat_is_neutral(self):
        t = _curve_text("走平", 36.0, None, 5.17, "驱动方向待确认")
        assert "信号中性" in t and "陡峭化" not in t and "平坦化" not in t

    def test_missing_spread_degrades(self):
        assert "数据不足" in _curve_text("数据不足", None, None, None, "驱动方向待确认")


class TestFedExpectationText:
    """联储预期段用 ZQ 期货隐含概率，不得再用 2Y−EFFR 符号猜加/降息。"""

    @staticmethod
    def _rates() -> pd.DataFrame:
        idx = pd.date_range("2026-09-21", periods=5)
        return pd.DataFrame({"DFEDTARL": [3.75] * 5, "DFEDTARU": [4.0] * 5}, index=idx)

    @staticmethod
    def _rex() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "meeting_date": ["2026-10-28", "2026-12-09"],
                "prob_cut": [0.0, 0.0],
                "prob_hold": [0.3, 0.0],
                "prob_hike": [0.7, 1.0],
                "expectation": ["加息", "加息"],
            },
            index=pd.to_datetime(["2026-09-28", "2026-09-28"]),
        )

    def test_uses_rex_probabilities(self):
        t = _fed_expectation_text(self._rates(), self._rex(), 3.88, 4.81)
        assert "联邦基金有效利率 3.88%（目标区间 3.75–4.00%）" in t
        assert "2026-10-28 FOMC" in t  # 取最新快照日的最近一场会议
        assert "加息 70%" in t
        assert "点阵图" not in t  # 数据里无点阵图，不得断言一致/背离

    def test_missing_rex_degrades(self):
        t = _fed_expectation_text(self._rates(), pd.DataFrame(), 3.88, 4.81)
        assert "3.88%" in t and "定价数据缺失" in t

    def test_missing_effr_degrades(self):
        t = _fed_expectation_text(self._rates(), self._rex(), None, None)
        assert "EFFR 数据缺失" in t and "加息 70%" in t


def _synthetic_rates() -> pd.DataFrame:
    """overview 所需最小 rates 表（40 个交易日）。"""
    idx = pd.date_range("2026-08-10", periods=40, freq="B")
    cols = [
        "DGS10",
        "DGS2",
        "DGS3MO",
        "DGS30",
        "DGS5",
        "DFF",
        "DGS1MO",
        "DFEDTARL",
        "DFEDTARU",
    ]
    return pd.DataFrame({c: 4.0 for c in cols}, index=idx)


class TestDriverLabel:
    """驱动单源判据：1 月内实际利率 vs 盈亏平衡的变动幅度。"""

    def test_real_dominates(self):
        assert _driver_label(17.0, 3.0) == "实际利率/期限溢价主导"

    def test_breakeven_dominates(self):
        assert _driver_label(-4.0, 9.0) == "通胀预期主导"

    def test_missing_degrades(self):
        assert _driver_label(None, 3.0) == "数据不足"


class TestDriverSingleSource:
    """回归：入口页曲线形态段与收益率曲线页的 driver 必须同一口径
    （原先 overview 按 10Y vs 2Y 另判一套「长端/短端驱动」，两页措辞互相矛盾）。"""

    def test_overview_text_uses_same_driver(self, monkeypatch):
        idx = pd.date_range("2026-08-01", periods=70)
        rates = pd.DataFrame(
            {
                "DGS2": 4.0,
                "DGS5": 4.2,
                "DGS10": 4.5,
                "DGS30": 4.8,
                "DGS3MO": 4.1,
                "DFF": 4.0,
                "DFEDTARL": 3.75,
                "DFEDTARU": 4.0,
            },
            index=idx,
        )
        tips = pd.DataFrame({"DFII10": [2.0] * 69 + [2.2]}, index=idx)  # 1 月 +20bp
        infl = pd.DataFrame({"T10YIE": 2.5}, index=idx)  # 1 月 0bp
        empty = pd.DataFrame()
        monkeypatch.setattr(
            ra,
            "_load",
            lambda: (rates, tips, infl, empty, empty, empty),
        )
        driver = yield_curve_analysis()["driver"]
        assert driver == "实际利率/期限溢价主导"
        assert driver in ra.overview_analysis()["sections"][0]["body"]


class TestOverviewDegradation:
    """tips/inflation/rex/auction 全缺时 overview 不得 500（原为 TypeError 崩溃）。"""

    def test_all_secondary_missing(self, monkeypatch):
        empty = pd.DataFrame()
        monkeypatch.setattr(
            ra, "_load", lambda: (_synthetic_rates(), empty, empty, empty, empty, empty)
        )
        out = ra.overview_analysis()
        assert len(out["sections"]) == 4
        assert "数据缺失" in out["sections"][1]["body"]  # 实际利率段
        assert "定价数据缺失" in out["sections"][2]["body"]  # 联储预期段
        assert out["effr"]["value"] == 4.0  # DFF 兜底仍在

    def test_dff_preferred_over_fedfunds(self, monkeypatch):
        """EFFR 口径与 /api/rates/fed-funds 一致：DFF 优先于月频 FEDFUNDS。"""
        rates = _synthetic_rates()
        rates["FEDFUNDS"] = 3.63
        empty6 = [pd.DataFrame()] * 5
        monkeypatch.setattr(
            ra,
            "_load",
            lambda: (rates, *empty6),
        )
        # DFF=4.0，非 FEDFUNDS=3.63
        assert ra.overview_analysis()["effr"]["value"] == 4.0


def test_yield_curve_analysis_has_global_long_end():
    """yield_curve 输出含全球长端对照（美/日/中），中国行来自 cgb.csv。"""
    out = yield_curve_analysis()
    markets = {g["market"]: g for g in out["global_long_end"]}
    assert set(markets) == {"美国", "日本", "中国"}
    cn = markets["中国"]
    if cn["rate"] is None:
        assert cn["source"] == "chinamoney RtimeYldCurv · daily"  # 未拉过数据不伪造
    else:
        assert cn["rate30"] is not None
        assert cn["spread_vs_us"] is not None
        assert cn["spread30_vs_us"] is not None
