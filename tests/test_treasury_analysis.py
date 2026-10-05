"""treasury_analysis 规则引擎单元测试（单位换算 / 官方占比 / 冒烟）。"""

import pandas as pd
import pytest

from src.treasury_analysis import (
    _b,
    _coupon_size_guided,
    _pct_rank,
    _t,
    generate_treasury_overview,
    official_share_series,
    rank_label,
)


def _monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


class TestUnits:
    def test_to_trillions(self):
        assert _t(9_371_073.0) == 9.37
        assert _t(None) is None

    def test_to_billions(self):
        assert _b(13_104.0) == 13.1
        assert _b(None) is None


class TestOfficialShare:
    def test_ffill_align(self):
        # TIC 月中发布上月数据，mspd 月末发布——ffill 对齐后应得 50%
        tic = pd.DataFrame(
            {"TIC_HOLD_OFFICIAL": _monthly([100.0, 110.0, 120.0])},
            index=pd.date_range("2020-01-01", periods=3, freq="MS"),
        )
        mspd = pd.DataFrame(
            {"TOTAL_DEBT": [200.0, 220.0]},
            index=pd.to_datetime(["2020-01-15", "2020-02-15"]),
        )
        share = official_share_series(tic, mspd)
        assert share.iloc[-1] == pytest.approx(120 / 220 * 100)

    def test_empty(self):
        assert official_share_series(pd.DataFrame(), pd.DataFrame()).empty


class TestRankLabel:
    """分位要说人话：「0 分位」在页面上读不通，极端值走最高/最低。"""

    def test_extremes(self):
        assert rank_label(0.0) == "全历史最低"
        assert rank_label(99.5) == "全历史最高"

    def test_mid(self):
        assert rank_label(63.3) == "全历史 63 分位"
        assert rank_label(None) == ""

    def test_pct_rank_strict_less(self):
        s = _monthly([10.0, 20.0, 30.0])
        assert _pct_rank(s, 20.0) == pytest.approx(33.3)
        assert _pct_rank(pd.Series(dtype=float), 1.0) is None


class TestCouponSizeGuidance:
    """附息债规模指引从 QRA 正文抽；措辞对不上就返回 None（研判不猜）。"""

    def test_maintain(self):
        body = (
            "Treasury anticipates maintaining nominal coupon and FRN auction "
            "sizes for at least the next several quarters."
        )
        assert _coupon_size_guided(body) == "maintain"

    def test_increase(self):
        body = "Treasury plans to modestly increase nominal coupon auction sizes."
        assert _coupon_size_guided(body) == "increase"

    def test_unrecognized_is_none(self):
        body = "Treasury will do something else this quarter."
        assert _coupon_size_guided(body) is None


def test_generate_smoke():
    """真实数据冒烟：cards / signals / 图表序列齐全。"""
    out = generate_treasury_overview()
    assert "error" not in out
    assert out["cards"]["hold_total"]["value"] > 0
    assert out["cards"]["official_share"]["value"] < 23  # 近年官方占比持续低于警戒线
    assert len(out["signals"]) == 3
    assert len(out["holdings_history"]["dates"]) > 60
    assert out["refunding"]["quarter"]


def test_bill_share_has_year_change():
    """Bill 占比 1Y 变化算在 MSPD 月频轴上。

    日频派生序列只有 30+ 行，取 250 个交易日前的值永远为 None
    ——卡片副标题永远只剩一个日期。
    """
    out = generate_treasury_overview()
    assert out["cards"]["bill_share"]["chg_1y"] is not None
    assert out["cards"]["bill_share"]["pct_label"]


def test_signals_use_percentile_not_dead_threshold():
    """官方占比 <23% 自 2015-08 起恒成立，研判得给出分位才有信息量。"""
    out = generate_treasury_overview()
    text = out["signals"][0]["text"]
    assert "全历史" in text
