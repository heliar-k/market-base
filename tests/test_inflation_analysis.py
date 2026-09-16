"""inflation_analysis 规则引擎单元测试（YoY 计算 / 卡片 / 信号 / 冒烟）。"""

import pandas as pd
import pytest

from src.inflation_analysis import (
    _card,
    _dir_label,
    _yoy_series,
    generate_inflation_overview,
    recent_rows,
    signal_level,
)


def _monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


class TestYoySeries:
    def test_basic(self):
        s = _monthly([100.0] * 12 + [110.0] * 12)
        yoy = _yoy_series(s)
        assert yoy.iloc[-1] == pytest.approx(10.0)
        assert len(yoy) == 12  # 前 12 个月无同比基数

    def test_nan_safe(self):
        # outer join 下月频序列夹 NaN，dropna 后位移仍按月度对齐
        s = _monthly([100.0] * 12 + [105.0] * 12)
        s = s.reindex(pd.date_range("2020-01-01", periods=48, freq="MS"))
        yoy = _yoy_series(s)
        assert yoy.iloc[-1] == pytest.approx(5.0)


class TestCard:
    def test_value_and_chg(self):
        s = _monthly([100.0] * 12 + [110.0, 110.0, 111.0])
        c = _card(s)
        assert c["value"] == pytest.approx((111 / 100 - 1) * 100)
        assert c["chg_1m"] == pytest.approx((111 - 110) / 100 * 100, abs=0.01)
        assert c["as_of"] == "2021-03-01"

    def test_insufficient_history(self):
        assert _card(_monthly([100.0] * 12)) == {}


class TestDirLabel:
    def test_thresholds(self):
        assert "回升" in _dir_label(0.2)
        assert "回落" in _dir_label(-0.2)
        assert _dir_label(0.02) == "基本持平"
        assert _dir_label(None) == "持平"


class TestSignalLevel:
    def test_above_target(self):
        cards = {
            "cpi": {"value": 3.5, "chg_1m": -0.1, "as_of": "2026-07-01"},
            "core_cpi": {"value": 2.8, "chg_1m": 0.0, "as_of": "2026-07-01"},
            "core_pce": {"value": 3.3, "chg_1m": -0.1, "as_of": "2026-06-01"},
        }
        text = signal_level(cards)
        assert "3.5%" in text and "1.3pp" in text and "尚未完成" in text

    def test_below_target(self):
        cards = {
            "cpi": {"value": 1.9, "chg_1m": -0.1, "as_of": "2026-07-01"},
            "core_cpi": {"value": 1.8, "chg_1m": 0.0, "as_of": "2026-07-01"},
            "core_pce": {"value": 1.9, "chg_1m": -0.1, "as_of": "2026-06-01"},
        }
        assert "约束基本解除" in signal_level(cards)


def test_recent_rows_keeps_ppi_only_month():
    """PPI 比 CPI 早一期发布：只有 PPI 有值的最新月也要出现在表首行。"""
    idx = pd.date_range("2024-01-01", periods=30, freq="MS")
    df = pd.DataFrame(
        {
            "CPI": range(100, 129),
            "CORE_CPI": range(100, 129),
            "CORE_PCE": range(100, 129),
        },
        index=idx[:-1],  # CPI 只到 2026-05
    ).astype(float)
    ppi = pd.DataFrame({"PPI_FD": [*range(100, 129), 130.0]}, index=idx)

    rows = recent_rows(df, ppi, n=12)

    assert rows[0]["date"] == "2026-06-01"
    assert rows[0]["cpi_yoy"] is None
    assert rows[0]["ppi_yoy"] == pytest.approx(11.11)  # 130/117-1
    assert len(rows) == 12


def test_generate_smoke():
    """真实数据冒烟：cards / signals / 图表序列齐全（数据由 daily-fetch 落库）。"""
    out = generate_inflation_overview()
    assert "error" not in out
    assert out["cards"]["core_pce"]["value"] is not None
    assert (
        len(out["signals"]) >= 3
    )  # 第 4 段「预测市场」数据驱动，有 Polymarket 快照才出现
    assert len(out["yoy_history"]["dates"]) > 60
    assert out["shapiro"]["core"]["supply"] is not None
