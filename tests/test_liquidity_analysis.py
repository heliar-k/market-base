"""liquidity_analysis 规则引擎单元测试（评分分档 / LPI 冒烟 / 前瞻日历）。"""

import pandas as pd
import pytest

from src.liquidity_analysis import (
    _band,
    _pct_chg,
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
        # 准备金 4 周变化型：越负分越高（阈值升序，找第一个 v <= th，未命中回退末档）
        assert _score(-4.4, [(-5, 9), (-3, 7), (-1, 5), (0, 3)]) == 7
        assert _score(-6.0, [(-5, 9), (-3, 7), (-1, 5), (0, 3)]) == 9
        assert _score(-2.0, [(-5, 9), (-3, 7), (-1, 5), (0, 3)]) == 5
        assert _score(0.5, [(-5, 9), (-3, 7), (-1, 5), (0, 3)]) == 3

    def test_missing_value(self):
        assert _score(None, [(0, 3), (-1, 5)]) is None

    def test_band(self):
        assert _band(2.0)[0] == "宽松"
        assert _band(4.0)[0] == "中性"
        assert _band(6.0)[0] == "警戒观察"
        assert _band(8.0)[0] == "压力确认"
        assert _band(None)[0] == "未知"


class TestPctChg:
    """_pct_chg() 的近零基数守卫（审计第 4 步）。"""

    def test_normal_base(self):
        s = pd.Series([100.0, 105.0], index=pd.date_range("2024-01-01", periods=2, freq="D"))
        assert _pct_chg(s, 1) == pytest.approx(5.0)

    def test_near_zero_base_is_noise(self):
        # RRP 型：历史峰值 2553B，基数只剩 0.5B —— +1900% 不是信号
        s = pd.Series([2553.0, 0.5, 10.0], index=pd.date_range("2024-01-01", periods=3, freq="D"))
        assert _pct_chg(s, 1) is None

    def test_all_zero_series_no_division(self):
        s = pd.Series([0.0, 0.0, 0.0], index=pd.date_range("2024-01-01", periods=3, freq="D"))
        assert _pct_chg(s, 1) is None


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
        assert "lpi_summary" in s and s["lpi_summary"].get("score") is not None
        assert isinstance(s.get("series_1y"), list) and len(s["series_1y"]) > 0

    def test_nl_pulse_and_subpages_consistency(self):
        from src.liquidity_analysis import (
            SRF_SUBSTANTIAL_B,
            _sofr_iorb_state,
            page_fed_balance_sheet,
            page_operations,
            page_reserves,
            page_rrp_tga,
            page_subsurface,
        )

        # 1. SOFR-IORB 状态词单源三档
        assert _sofr_iorb_state(-1.0) == "充裕"
        assert _sofr_iorb_state(0.0) == "中性偏紧"
        assert _sofr_iorb_state(2.5) == "中性偏紧"
        assert _sofr_iorb_state(4.0) == "偏紧"

        # 2. LPI 脉冲单位为十亿美元（B），非百万美元
        d = lpi()
        nl_part = next(
            p for p in d["layers"]["structure"]["parts"] if p["name"] == "净流动性20日脉冲"
        )
        assert nl_part["unit"] == "B"
        assert abs(nl_part["value"]) < 2000  # 十亿美元量级（非百万级）

        # 3. 资产负债表与准备金近 20 期仅含 H.4.1 周三有效值（无空行）
        bs = page_fed_balance_sheet()
        assert bs["rows"] and all(r["WALCL"] is not None for r in bs["rows"])
        res = page_reserves()
        assert "dealer_friction" in res["cards"]
        assert "sofr_tgcr_bp" in res["spreads"] and "effr_iorb_bp" in res["spreads"]

        # 4. SRF 实质动用门槛统一为 >= $50M（0.05B），且 rrp-tga 状态词与单源一致
        ops = page_operations()
        sub = page_subsurface()
        rt = page_rrp_tga()
        assert ops["srf"]["threshold_m"] == int(SRF_SUBSTANTIAL_B * 1000)
        assert sub["srf"]["threshold_m"] == int(SRF_SUBSTANTIAL_B * 1000)
        assert sub["srf"]["active_30d"] <= sub["srf"]["raw_30d"]
        assert rt["alert"]["state"] == _sofr_iorb_state(rt["alert"]["sofr_iorb_bp"])


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
        # 有新发抽水的结算日：验证到期回笼与未公布规模估算补齐生效
        # （未补齐 offering_amt=NaN 时 Bill 新发会被当成 0；未抵消到期则会显示全额 -290B）
        settle_days = [
            d for d in days if any(f["type"] == "auction_settlement" for f in d["flows"])
        ]
        assert settle_days, "14 天窗口内应有新发结算日（数据缺失或日历滚动？）"
        # 验证 BB_OAS 单位已统一为 bp（100~1000 量级，非 1~10 百分点）
        d_lpi = lpi()
        bb_part = next(
            p for p in d_lpi["layers"]["transmission"]["parts"] if p["name"] == "HY OAS(BB)"
        )
        assert bb_part["unit"] == "bp" and bb_part["value"] > 50
        assert d_lpi["evidence"]["intermediary"]["bb_oas"] > 50
        assert float(d_lpi["evidence"]["repo"]["rrp_b"].replace(",", "")) >= 0.1


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
