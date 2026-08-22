"""liquidity_analysis 规则引擎单元测试（评分分档 / LPI 冒烟 / 前瞻日历）。"""

import pandas as pd

from src.liquidity_analysis import (
    _band,
    _score,
    forward_calendar,
    liquidity_snapshot,
    lpi,
)


class TestScore:
    def test_positive_higher_band_descending(self):
        # VIX 型：值越大分越高，阈值降序，取首个命中
        assert _score(16.0, [(35, 9), (25, 7), (18, 5), (14, 3)]) == 3
        assert _score(26.0, [(35, 9), (25, 7), (18, 5), (14, 3)]) == 7
        assert _score(40.0, [(35, 9), (25, 7), (18, 5), (14, 3)]) == 9

    def test_negative_higher_band_ascending(self):
        # 准备金 4 周变化型：越负分越高（阈值升序，落空回退到最高分档）
        assert _score(-4.4, [(0, 3), (-1, 5), (-3, 7), (-5, 9)]) == 9
        assert _score(-2.0, [(0, 3), (-1, 5), (-3, 7), (-5, 9)]) == 7
        assert _score(0.5, [(0, 3), (-1, 5), (-3, 7), (-5, 9)]) == 3

    def test_missing_value(self):
        assert _score(None, [(0, 3), (-1, 5)]) is None

    def test_band(self):
        assert _band(2.0)[0] == "宽松"
        assert _band(4.0)[0] == "中性"
        assert _band(6.0)[0] == "警戒观察"
        assert _band(8.0)[0] == "压力确认"
        assert _band(None)[0] == "未知"


class TestLpiSmoke:
    def test_lpi_shape_and_range(self):
        d = lpi()
        assert d["score"] is not None
        assert 0 <= d["score"] <= 10
        assert set(d["layers"]) == {"structure", "funding", "transmission"}
        for layer in d["layers"].values():
            assert 0 <= layer["score"] <= 10
            assert layer["weight"] in (0.45, 0.35, 0.20)
        # 确认条件 6 条与参考页一致
        assert len(d["confirmations"]) == 6
        assert d["confirmations"][5]["title"].startswith("RRP 贴零")

    def test_snapshot_narrative(self):
        s = liquidity_snapshot()
        ev = s["evaluation"]
        assert {"net_liquidity", "outlook", "reserves"} <= set(ev)
        for sec in ev.values():
            if isinstance(sec, dict):
                assert sec["text"]
        assert s["data_date"] is not None


class TestForwardCalendar:
    def test_calendar_window_and_net(self):
        cal = forward_calendar(14)
        days = cal["days"]
        assert len(days) == 14
        # 每天 bucket 合法
        for d in days:
            assert d["bucket"] in {"extreme", "strong", "mild", "smooth", "injection"}
        # 7 日净冲击来自 DTS 历史（存在真实数据）
        assert cal["net_7d_b"] is not None
        # 8-27 有 bill 结算 → 应为净发行（抵消到期后仍为负或小正值），不越界极端
        day927 = next((d for d in days if d["date"].startswith("2026-08-27")), None)
        assert day927 is not None
        assert abs(day927["net_b"]) < 100, (
            f"净发行口径应抵消滚续，实际 {day927['net_b']}"
        )


class TestLpiSnapshot:
    def test_snapshot_writes_and_percentile(self, monkeypatch, tmp_path):
        """快照写入 lpi_history.csv（观测日 upsert）；历史 <10 条时分位为 None。"""
        from src import liquidity_analysis as la

        target = tmp_path / "lpi_history.csv"
        monkeypatch.setattr(la, "LPI_HISTORY_PATH", target)

        assert la.save_lpi_snapshot() == target
        hist = pd.read_csv(target, index_col=0)
        assert {"SCORE", "STRUCTURE", "FUNDING", "TRANSMISSION"} <= set(hist.columns)
        assert 0 < hist["SCORE"].iloc[0] <= 10
        assert la.lpi_percentile_30d() is None  # 仅 1 条

        # 伪造 30 条历史（当前分 5.7 高于 30 条中 24 条）→ 分位≈80
        import numpy as np

        rows = pd.DataFrame(
            {
                "SCORE": np.full(30, 5.0),
                "STRUCTURE": 7.0,
                "FUNDING": 4.8,
                "TRANSMISSION": 3.4,
            }
        )
        rows.iloc[-6:] = 5.4
        rows.index = pd.date_range("2026-07-01", periods=30)
        rows.to_csv(target)
        p = la.lpi_percentile_30d()
        assert p is not None and 75 <= p <= 100
