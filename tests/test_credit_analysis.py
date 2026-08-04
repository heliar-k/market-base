"""credit_analysis 规则引擎单元测试（分位 / 滚动分位 / 卡片 / 压力合成）。"""

import pandas as pd
import pytest

from src.credit_analysis import (
    STRESS_WEIGHTS,
    _latest_card,
    _liq_etf_cards,
    _overview_signals,
    _pct,
    _regime_score,
    _regime_zone,
    _rolling_pct,
    stress,
)


def _series(n: int, descending: bool = False, start: float = 1.0) -> pd.Series:
    """构造 n 条工作日序列（可选递减），数值为 start..start+n-1。"""
    vals = [start + i for i in range(n)]
    if descending:
        vals = vals[::-1]
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.Series(vals, index=idx)


class TestPct:
    def test_ascending_last_is_max(self):
        assert _pct(_series(300), 250) == 100.0

    def test_descending_last_is_min(self):
        # 递减序列：最新值最小，(<=) 占比 = 1/窗口
        assert _pct(_series(300, descending=True), 250) == pytest.approx(100 / 250)

    def test_window_full_marks_obs(self):
        obs = {}
        _pct(_series(300), 250, obs)
        assert obs == {"n": 250, "full": True}

    def test_short_series_marks_not_full(self):
        obs = {}
        _pct(_series(100), 250, obs)
        assert obs == {"n": 100, "full": False}

    def test_exact_window_is_full(self):
        # n == window 边界：尾窗恰好全量
        obs = {}
        assert _pct(_series(250), 250, obs) == 100.0
        assert obs == {"n": 250, "full": True}

    def test_empty_returns_none(self):
        assert _pct(pd.Series(dtype=float), 250) is None


class TestRollingPct:
    def test_below_min_periods_returns_empty(self):
        assert _rolling_pct(_series(59), 250, min_periods=60).empty

    def test_exactly_min_periods_produces_first_value(self):
        # len == min_periods 的边界：首个滚动值出现且基于 60 条
        out = _rolling_pct(_series(60, descending=True), 250, min_periods=60)
        assert len(out) == 60
        assert out.dropna().iloc[0] == pytest.approx(100 / 60)

    def test_ascending_peaks_at_100(self):
        out = _rolling_pct(_series(300), 250, min_periods=60)
        assert not out.empty
        assert out.dropna().eq(100).all()

    def test_window_len_governs_value(self):
        # 递减：窗口内最后一条最小 → 1/len(x)；首窗口 len=min_periods
        out = _rolling_pct(_series(300, descending=True), 250, min_periods=60)
        vals = out.dropna()
        assert vals.iloc[0] == pytest.approx(100 / 60)
        assert vals.iloc[-1] == pytest.approx(100 / 250)


class TestLatestCard:
    def test_missing_column(self):
        card, s = _latest_card(pd.DataFrame({"A": [1.0]}), "B", 1)
        assert card == {}
        assert s.empty

    def test_all_nan(self):
        df = pd.DataFrame(
            {"A": [float("nan"), float("nan")]},
            index=pd.date_range("2024-01-01", periods=2),
        )
        card, s = _latest_card(df, "A", 1)
        assert card == {}
        assert s.empty

    def test_value_and_as_of(self):
        df = pd.DataFrame(
            {"A": [0.12345, 0.6789]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        card, s = _latest_card(df, "A", 2)
        assert card == {"value": 0.68, "as_of": "2024-01-02"}
        assert len(s) == 2

    def test_scale_applies(self):
        df = pd.DataFrame({"A": [0.1234]}, index=pd.to_datetime(["2024-01-01"]))
        card, _ = _latest_card(df, "A", 1, scale=100)
        assert card["value"] == 12.3


class TestStressComposite:
    # 权重字面量（与 STRESS_WEIGHTS 一致）；用字面量重算才能捕获权重漂移
    W = (0.30, 0.20, 0.20, 0.15, 0.15)

    def test_weights_sum_to_one(self):
        assert sum(STRESS_WEIGHTS.values()) == pytest.approx(1.0)

    def test_weighted_sum_matches_weights(self):
        # 全部递增：分位=100，mom 由 22 日变化（22bp）决定，div=0
        df_vol = pd.DataFrame(
            {
                "HY_OAS": _series(300) / 100,
                "IG_OAS": _series(300) / 100,
                "VIX": _series(300),
            }
        )
        out = stress(df_vol, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["hy"] == 100
        assert comp["mom"] == pytest.approx(11.0)  # 22bp / 2
        assert comp["div"] == 0
        wh, wi, wm, wv, wd = self.W
        assert out["composite"] == round(
            wh * comp["hy"]
            + wi * comp["ig"]
            + wm * comp["mom"]
            + wv * comp["vix"]
            + wd * comp["div"],
            1,
        )
        assert out["zone"] == "中性"

    def test_divergence_weight_applied(self):
        # 信用走松（hy 递减→0.4）但股市紧张（vix 升序→100）→ div≈99.6 真正贡献权重
        df_vol = pd.DataFrame(
            {
                "HY_OAS": _series(300, descending=True) / 100,
                "IG_OAS": _series(300) / 100,
                "VIX": _series(300),
            }
        )
        out = stress(df_vol, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["div"] == pytest.approx(99.6)
        wh, wi, wm, wv, wd = self.W
        assert out["composite"] == round(
            wh * comp["hy"]
            + wi * comp["ig"]
            + wm * comp["mom"]
            + wv * comp["vix"]
            + wd * comp["div"],
            1,
        )

    def test_ig_and_vix_weights_distinct(self):
        # ig_pct=100 但 vix_pct=0.4：ig/vix 权重互换（0.20↔0.15）必须被捕获
        df_vol = pd.DataFrame(
            {
                "HY_OAS": _series(300, descending=True) / 100,
                "IG_OAS": _series(300) / 100,
                "VIX": _series(300, descending=True),
            }
        )
        out = stress(df_vol, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["ig"] == 100
        assert comp["vix"] == pytest.approx(0.4)
        wh, wi, wm, wv, wd = self.W
        assert out["composite"] == round(
            wh * comp["hy"]
            + wi * comp["ig"]
            + wm * comp["mom"]
            + wv * comp["vix"]
            + wd * comp["div"],
            1,
        )

    def test_empty_frames_degrades_to_zero(self):
        out = stress(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        assert out["composite"] == 0
        assert out["zone"] == "宽松"
        # 数据驱动组件的 raw 为 None（div 是派生量恒为 0.0）
        assert out["components"][0]["raw"] is None
        assert out["history"] is None

    def test_missing_vix_column_degrades(self):
        # VIX 缺失 → vix_pct=0，div=|hy_pct-0| 假性拉满（现状锁定，不修）
        df_vol = pd.DataFrame({"HY_OAS": _series(300) / 100})
        out = stress(df_vol, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["vix"] == 0
        assert comp["div"] == pytest.approx(100)
        assert out["composite"] == pytest.approx(0.30 * 100 + 0.20 * 11.0 + 0.15 * 100)

    def test_missing_hy_column_degrades(self):
        # HY_OAS 缺失：hy_pct=0、raw=None，不抛异常
        df_vol = pd.DataFrame({"IG_OAS": _series(300) / 100, "VIX": _series(300)})
        out = stress(df_vol, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["hy"] == 0
        assert out["components"][0]["raw"] is None


class TestRegimeScore:
    def test_composite_is_equal_weight_average(self):
        # 全部递增：各分位=100，mom=61（+22bp/2），supply=10，liq clamp 到 0
        n = 300
        df_vol = pd.DataFrame(
            {"HY_OAS": _series(n) / 100, "IG_OAS": _series(n) / 100, "VIX": _series(n)}
        )
        df_cr = pd.DataFrame(
            {
                "HY_YIELD": _series(n) / 100,
                "IG_YIELD": _series(n) / 100,
                "SLOOS_CI_STD": _series(n).where(_series(n) <= 10, 10.0),
                "DELINQ_CI": _series(45, descending=True),
                "DELINQ_CRE": _series(45, descending=True),
                "DELINQ_CC": _series(45, descending=True),
                "CHGOFF_BUS": _series(45, descending=True),
                "CHGOFF_CONS": _series(45, descending=True),
            }
        )
        df_yf = pd.DataFrame({"HYG": _series(n), "LQD": _series(n)})
        out = _regime_score(df_vol, df_cr, df_yf)
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["spread_level"] == 100
        assert comp["spread_mom"] == 61.0  # 50 + 22bp/2
        assert comp["funding_cost"] == 100
        assert comp["credit_supply"] == 10.0
        assert comp["credit_quality"] == pytest.approx(2.5)  # 100/40 递减
        assert comp["market_liq"] == 0.0
        assert comp["cross_asset"] == 100
        vals = [v for v in comp.values() if v is not None]
        assert out["score"] == round(sum(vals) / len(vals), 1)
        assert out["regime"] == "neutral tightening"

    def test_supply_zero_when_easing(self):
        # SLOOS 负值（放松标准）→ 0 分，非数据缺失（对齐原站口径）
        df_cr = pd.DataFrame({"SLOOS_CI_STD": [10.0, 5.0, -5.7]})
        out = _regime_score(pd.DataFrame(), df_cr, pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["credit_supply"] == 0.0

    def test_missing_data_degrades_to_available_average(self):
        # 只有 HY_OAS + VIX：6 项缺失 → 综合 = 2 项平均，missing 列表列出
        df_vol = pd.DataFrame({"HY_OAS": _series(300) / 100, "VIX": _series(300)})
        out = _regime_score(df_vol, pd.DataFrame(), pd.DataFrame())
        comp = {c["key"]: c["value"] for c in out["components"]}
        assert comp["spread_level"] is None
        assert comp["market_liq"] is None
        assert comp["credit_supply"] is None
        assert out["score"] == pytest.approx(80.5)  # (mom 61 + cross 100)/2，其余降级
        assert "Market Liquidity" in out["missing"]

    def test_zone_boundaries(self):
        assert _regime_zone(0)[0] == "easing"
        assert _regime_zone(24.9)[0] == "easing"
        assert _regime_zone(25)[0] == "neutral easing"
        assert _regime_zone(49.9)[0] == "neutral easing"
        assert _regime_zone(50)[0] == "neutral tightening"
        assert _regime_zone(100)[0] == "tightening"


class TestOverviewSignals:
    def test_three_part_framework(self):
        hy = {"value": 284.0, "pct_10y": 27.0}
        funding = {"hy": {"value": 716.0}}
        sloos = [{"name": "C&I 贷款标准", "value": -5.7}]
        fincond = {"nfci": {"value": -0.55}}
        s = _overview_signals(hy, funding, sloos, fincond)
        assert s["framework"].startswith("What changed")
        assert "284.0bp" in s["what_changed"]
        assert "7.16%" in s["why_it_matters"]
        assert "SLOOS 是否转正确认银行收紧" in s["what_to_watch"]
        assert "NFCI 是否从 -0.55 附近上行" in s["what_to_watch"]

    def test_tightening_branch(self):
        # 银行收紧 + 融资成本高位 → 估值弹性先被压缩
        funding = {"hy": {"value": 716.0}}
        sloos = [{"name": "C&I 贷款标准", "value": 15.0}]
        s = _overview_signals({}, funding, sloos, {})
        assert "收紧" in s["what_changed"]
        assert "估值弹性会先被压缩" in s["why_it_matters"]
        assert "SLOOS 是否继续确认银行收紧" in s["what_to_watch"]

    def test_missing_data_degrades(self):
        s = _overview_signals({}, {}, [], {})
        assert "数据缺失" in s["what_changed"]
        assert s["what_to_watch"] == "下一步看 SLOOS 与 HY OAS 数据是否延续。"


class TestLiqEtfVolume:
    def test_volume_read_from_snapshot(self):
        # 快照带 volume 列 → 卡片含 volume；无 volume 列 → 容忍为 None
        idx = pd.to_datetime(["2026-08-01", "2026-08-04"])
        df = pd.DataFrame(
            {"HYG": [79.31, 79.10], "HYG_volume": [30_000_000, 29_500_000]},
            index=idx,
        )
        out = _liq_etf_cards(df)
        assert out["hyg"]["volume"] == 29_500_000

    def test_missing_volume_column_tolerated(self):
        idx = pd.to_datetime(["2026-08-01", "2026-08-04"])
        df = pd.DataFrame({"HYG": [79.31, 79.10]}, index=idx)
        out = _liq_etf_cards(df)
        assert out["hyg"]["value"] == 79.1
        assert out["hyg"]["volume"] is None
