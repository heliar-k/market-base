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
    def test_lpi_no_recursion(self, monkeypatch):
        """回归：lpi() 不得递归触发自身（曾嵌套 325 次/15s 的互递归 bug）。"""
        from src import liquidity_analysis as la

        calls = [0]
        orig = la.lpi

        def counted():
            calls[0] += 1
            return orig()

        monkeypatch.setattr(la, "lpi", counted)
        la.lpi()
        assert calls[0] == 1

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
        # 历史序列（供 30 天走势图）始终为 list
        assert isinstance(d["history"], list)

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
        # 净冲击 = 未来窗口逐日之和（与参考页口径一致，非历史 DTS）
        assert cal["net_7d_b"] is not None
        assert cal["net_7d_b"] == round(sum(x["net_b"] for x in days[:7]), 1)
        assert cal["net_14d_b"] == round(sum(x["net_b"] for x in days[:14]), 1)
        # 有新发抽水的 bill 结算日：同日到期抵消后净冲击应远小于毛发行额，不越界极端
        settle_days = [
            d
            for d in days
            if any(f["type"] == "auction_settlement" for f in d["flows"])
        ]
        assert settle_days, "14 天窗口内应有 bill 新发结算日（数据缺失或日历滚动？）"
        for d in settle_days:
            assert abs(d["net_b"]) < 100, (
                f"净发行口径应抵消滚续，实际 {d['date']} {d['net_b']}"
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
        assert la.lpi_percentile_30d(5.7) is None  # 仅 1 条

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
        p = la.lpi_percentile_30d(5.7)
        assert p is not None and 75 <= p <= 100
