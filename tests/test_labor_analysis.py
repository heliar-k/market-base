"""labor_analysis 规则引擎单元测试（Sahm / V/U / 信号 / 冒烟）。"""

import pandas as pd
import pytest

from src.labor_analysis import (
    challenger_block,
    decline_streak,
    employment_structure,
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


def test_decline_streak_counts_only_latest_run() -> None:
    s = _monthly([100.0, 99.0, 98.0, 98.5, 98.0, 97.0])
    assert decline_streak(s) == 2


def test_employment_structure_computes_wage_productivity_gap() -> None:
    labor = pd.DataFrame(
        {"PROF_BUSINESS_EMP": [100.0, 99.0, 98.0, 97.0]},
        index=pd.date_range("2026-01-01", periods=4, freq="MS"),
    )
    lm = pd.DataFrame(
        {
            "PRODUCTIVITY": [100.0, 101.0, 102.0, 103.0, 104.0],
            "REAL_HOURLY_COMP": [100.0, 102.0, 104.0, 106.0, 108.0],
        },
        index=pd.date_range("2025-01-01", periods=5, freq="QS"),
    )
    growth = pd.DataFrame({"NBER_RECESSION": [0.0]}, index=pd.to_datetime(["2026-09-01"]))
    out = employment_structure(labor, lm, growth)
    assert out["employment"][0]["down_periods"] == 3
    assert out["wage_productivity"]["gap_pp"] == pytest.approx(4.0)
    assert out["nber"]["in_recession"] is False


def test_challenger_block_preserves_monthly_ytd_and_basis() -> None:
    frame = pd.DataFrame(
        {
            "job_cuts_monthly": [43281],
            "ai_job_cuts_monthly": [3462],
            "ai_share_monthly_pct": [8.0],
            "job_cuts_ytd": [529914],
            "ai_job_cuts_ytd": [116175],
            "ai_share_ytd_pct": [21.9],
            "ai_monthly_basis": ["ytd_difference"],
            "published_at": ["2026-10-01"],
            "source_url": ["https://www.challengergray.com/report"],
        },
        index=pd.to_datetime(["2026-09-01"]),
    )
    out = challenger_block(frame)
    assert out["ai_job_cuts_monthly"] == 3462
    assert out["ai_share_ytd_pct"] == pytest.approx(21.9)
    assert out["ai_monthly_basis"] == "ytd_difference"
    assert out["as_of"] == "2026-09-01"


def test_generate_smoke():
    """真实数据冒烟：cards / signals / 图表序列齐全。"""
    out = generate_labor_overview()
    assert "error" not in out
    assert out["cards"]["unrate"]["value"] > 0
    assert out["cards"]["jolts"]["vu"] is not None
    assert len(out["signals"]) >= 3  # 第 4 段「预测市场」数据驱动，有 Polymarket 快照才出现
    assert len(out["nfp_history"]["dates"]) == 36
