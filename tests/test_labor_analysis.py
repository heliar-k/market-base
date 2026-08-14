"""labor_analysis 规则引擎单元测试（Sahm / V/U / 信号 / 冒烟）。"""

import pandas as pd
import pytest

from src.labor_analysis import (
    generate_labor_overview,
    sahm_rule,
    signal_current,
    vu_ratio,
)


def _monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


class TestSahmRule:
    def test_triggered(self):
        # 前 12 个月 3.5，近 3 个月跳到 4.2 → 3M 均值 4.2 − 低点 3.5 = 0.7 ≥ 0.5
        u = _monthly([3.5] * 12 + [4.2, 4.2, 4.2])
        assert sahm_rule(u)["value"] == pytest.approx(0.7, abs=0.01)

    def test_calm(self):
        u = _monthly([4.0] * 15)
        assert sahm_rule(u)["value"] == pytest.approx(0.0)

    def test_insufficient(self):
        assert sahm_rule(_monthly([4.0] * 5)) == {}


class TestVuRatio:
    def test_basic(self):
        lm = pd.DataFrame(
            {"JOLTS_OPEN": [7000.0, 7200.0], "UNEMPLOY": [7000.0, 6000.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="MS"),
        )
        vu = vu_ratio(lm)
        assert vu.iloc[-1] == pytest.approx(1.2)

    def test_missing_cols(self):
        assert vu_ratio(pd.DataFrame({"X": [1.0]})).empty


class TestSignalCurrent:
    def test_weak_nfp(self):
        cards = {
            "unrate": {"value": 4.3, "chg_3m": 0.2},
            "nfp": {"value": 50.0, "avg_3m": 80.0},
            "icsa": {"value": 220.0, "avg_4w": 215.0},
        }
        text = signal_current(cards, {"value": 0.1})
        assert "4.3%" in text and "明显减速" in text


def test_generate_smoke():
    """真实数据冒烟：cards / signals / 图表序列齐全。"""
    out = generate_labor_overview()
    assert "error" not in out
    assert out["cards"]["unrate"]["value"] > 0
    assert out["cards"]["jolts"]["vu"] is not None
    assert len(out["signals"]) == 3
    assert len(out["nfp_history"]["dates"]) == 36
