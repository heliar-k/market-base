"""rates_analysis 规则引擎单元测试（形态判定 / 失效条件 / 盈亏平衡 / 交易目标）。"""

import pandas as pd
import pytest

from src.rates_analysis import (
    _breakeven,
    _coupon_cover,
    _invalidation,
    _shape_label,
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


def test_yield_curve_analysis_has_global_long_end():
    """yield_curve 输出含全球长端对照（美/日/中），中国为待接入。"""
    out = yield_curve_analysis()
    markets = {g["market"]: g for g in out["global_long_end"]}
    assert set(markets) == {"美国", "日本", "中国"}
    assert markets["中国"]["rate"] is None  # 待接入，不伪造数值
