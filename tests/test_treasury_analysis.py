"""treasury_analysis 规则引擎单元测试（单位换算 / 官方占比 / 冒烟）。"""

import pandas as pd
import pytest

from src.treasury_analysis import (
    _b,
    _t,
    generate_treasury_overview,
    official_share_series,
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


def test_generate_smoke():
    """真实数据冒烟：cards / signals / 图表序列齐全。"""
    out = generate_treasury_overview()
    assert "error" not in out
    assert out["cards"]["hold_total"]["value"] > 0
    assert out["cards"]["official_share"]["value"] < 23  # 近年官方占比持续低于警戒线
    assert len(out["signals"]) == 3
    assert len(out["holdings_history"]["dates"]) > 60
    assert out["refunding"]["quarter"]
