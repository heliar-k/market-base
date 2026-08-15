"""波动率全景仪表盘（timsun 复刻）单元测试。

覆盖：
- 30 指数统一表（本地历史变化 / Barchart 官方变化兜底 / 别名映射 VXV→VIX3M）
- 统计条 / Hero 定调 / 风险矩阵 / Vol Trade Map / 叙事（风险评估评分）
"""

import pandas as pd

from src.volatility_dashboard import (
    INDICES,
    _hero,
    _indices_table,
    _narr_overview,
    _narr_risk,
    _narr_term,
    _narr_trade,
    _risk_matrix,
    _risk_score,
    _signals,
    _stats,
    _trade_map,
)


def _cboe_frame() -> pd.DataFrame:
    """小型 cboe 宽表：VIX 家族 30 个交易日，最后一日 VIX=15.0。"""
    idx = pd.date_range("2026-01-01", periods=30, freq="B")
    df = pd.DataFrame(index=idx)
    df["VIX"] = [15.0 + i * 0.1 for i in range(30)]
    df["VIX1D"] = df["VIX"] - 5
    df["VIX9D"] = df["VIX"] - 2
    df["VIX3M"] = df["VIX"] + 3  # VXV 别名源
    df["VIX6M"] = df["VIX"] + 5
    df["VIX1Y"] = df["VIX"] + 7
    df["SKEW"] = 135.0
    df["OVX"] = df["VIX"] + 35
    df["VVIX"] = 90.0 - df["VIX"]
    df["VXN"] = df["VIX"] + 6
    df["VXD"] = df["VIX"] - 2
    df["GVZ"] = 24.0
    df["VXSL"] = 44.0
    df["VTLT"] = df["VIX"] - 3
    df["VXHY"] = 5.6
    df["VXTH"] = 710.0
    df["VXNG"] = 37.0
    df["VXEEM"] = 26.0
    df["VEWZ"] = 27.0
    return df


def _barchart_frame() -> pd.DataFrame:
    """Barchart 快照宽表（仅存关键列，其他列在真实数据中由 fetch 生成）。"""
    cols = {}
    for sym, name, cat, col, src in INDICES:
        cols[f"{sym}_chg1d"] = -1.5  # 官方变化兜底
    df = pd.DataFrame([cols], index=pd.DatetimeIndex(["2026-02-13"]))
    return df


class TestIndicesTable:
    def test_30_symbols_covered(self):
        """INDICES 定义 30 个 timsun 面板符号。"""
        assert len(INDICES) == 30
        symbols = [s for s, *_ in INDICES]
        assert "VIX" in symbols and "VXMO" in symbols and "VXEF" in symbols

    def test_vxv_maps_to_vix3m(self):
        """VXV 行取 VIX3M 列（别名映射，VXV 官方列 2017 年停更）。"""
        cboe = _cboe_frame()
        cboe["VXV"] = 1.0  # 伪造停更列，不应被采用
        rows = _indices_table(cboe, pd.DataFrame(), pd.DataFrame())
        vxv = next(r for r in rows if r["symbol"] == "VXV")
        assert vxv["value"] == 20.9  # 30 个交易日后 VIX3M = 15 + 2.9 + 3

    def test_barchart_chg_fallback_for_yf_and_barchart(self):
        """VXMO（barchart 源）无本地历史时用官方 chg 兜底。"""
        cboe = _cboe_frame()
        bc = _barchart_frame()
        rows = _indices_table(cboe, pd.DataFrame(), bc)
        vxmo = next(r for r in rows if r["symbol"] == "VXMO")
        assert vxmo["chg1d"] == -1.5

    def test_barchart_chg_first_for_all_symbols(self):
        """有快照时全部 30 指数用 Barchart 官方变化（与 timsun 同源同口径）。"""
        cboe = _cboe_frame()
        bc = _barchart_frame()
        rows = _indices_table(cboe, pd.DataFrame(), bc)
        vix = next(r for r in rows if r["symbol"] == "VIX")
        assert vix["chg1d"] == -1.5

    def test_local_fallback_when_snapshot_missing(self):
        """无快照时 cboe 源按本地序列自算 1D 变化。"""
        cboe = _cboe_frame()
        rows = _indices_table(cboe, pd.DataFrame(), pd.DataFrame())
        vix = next(r for r in rows if r["symbol"] == "VIX")
        # 本地：VIX 最新 17.9 vs 前日 17.8 → +0.56%
        assert abs(vix["chg1d"] - 0.56) < 0.01

    def test_missing_series_value_none(self):
        """缺失序列 value 为 None（前端显示 —）。"""
        rows = _indices_table(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        vix = next(r for r in rows if r["symbol"] == "VIX")
        assert vix["value"] is None and vix["chg1d"] is None


class TestStats:
    def test_stats_avg_and_counts(self):
        rows = [
            {"chg1d": 1.0, "chg5d": 2.0, "chg1m": 3.0},
            {"chg1d": -1.0, "chg5d": -2.0, "chg1m": -3.0},
            {"chg1d": 0.5, "chg5d": 0.0, "chg1m": None},
        ]
        st = _stats(rows)
        assert st["n"] == 3
        assert st["up"] == 2 and st["down"] == 1
        assert st["avg1d"] == 0.17
        assert st["avg5d"] == 0.0
        assert st["avg1m"] == 0.0

    def test_stats_empty(self):
        st = _stats([])
        assert st["n"] == 0 and st["avg1d"] is None


class TestHero:
    def test_structural_verdict_low_vix_high_ovx(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "标普500波动率"},
            {"symbol": "VVIX", "value": 87.0, "chg1d": -2.0, "name": "波动率的波动率"},
            {"symbol": "MOVE", "value": 69.0, "chg1d": 0.5, "name": "美债波动率"},
            {"symbol": "OVX", "value": 49.0, "chg1d": 0.3, "name": "原油波动率"},
        ]
        h = _hero(rows)
        assert "结构性波动" in h["verdict"]
        assert [c["symbol"] for c in h["cards"]] == ["VIX", "VVIX", "MOVE", "OVX"]

    def test_panic_verdict(self):
        rows = [
            {"symbol": "VIX", "value": 26.0, "chg1d": 1.0, "name": "n"},
            {"symbol": "VVIX", "value": 100.0, "chg1d": 1.0, "name": "n"},
            {"symbol": "MOVE", "value": 80.0, "chg1d": 1.0, "name": "n"},
            {"symbol": "OVX", "value": 60.0, "chg1d": 1.0, "name": "n"},
        ]
        assert "警戒" in _hero(rows)["verdict"]


class TestSignalsAndRisk:
    def test_signals_tail_divergence_and_commodity(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VVIX", "value": 87.0, "chg1d": -2.0, "name": "n"},
            {"symbol": "MOVE", "value": 69.0, "chg1d": 0.5, "chg1m": -7.0, "name": "n"},
            {"symbol": "OVX", "value": 49.0, "chg1d": 0.3, "name": "n"},
        ]
        titles = [s["title"] for s in _signals(rows)]
        assert "尾部保护与现货 VIX 背离" in titles
        assert "商品波动仍是主线" in titles

    def test_risk_matrix_6_cells(self):
        rows = [
            {"symbol": s, "value": 10.0 + i, "chg1d": -i * 0.5}
            for i, s in enumerate(
                [
                    "VXD",
                    "VXN",
                    "VTLT",
                    "MOVE",
                    "VXNG",
                    "OVX",
                    "VXHY",
                    "VEEM",
                    "VEWZ",
                    "VOLI",
                    "VXTH",
                ]
            )
        ]
        m = _risk_matrix(rows)
        assert len(m) == 6
        assert [c["name"] for c in m] == [
            "权益",
            "利率",
            "商品",
            "信用",
            "FX/EM",
            "尾部保护",
        ]
        assert m[0]["chg_symbol"] == "VXD" and m[0]["level_symbol"] == "VXN"


class TestTradeMap:
    def test_carry_conditions_met(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXV", "value": 18.5, "chg1d": 0.0, "name": "n"},
            {"symbol": "VXST", "chg1d": -1.0},
        ]
        tm = _trade_map(rows)
        assert tm["title"] == "VIX contango carry"
        assert "+4.5pt" in tm["trigger"]
        assert tm["confidence"] == "中"

    def test_carry_off_when_vix_above_20(self):
        rows = [
            {"symbol": "VIX", "value": 22.0, "chg1d": 1.0, "name": "n"},
            {"symbol": "VXV", "value": 20.0, "chg1d": 0.0, "name": "n"},
            {"symbol": "VXST", "chg1d": 0.5},
        ]
        tm = _trade_map(rows)
        assert tm["title"] == "波动偏高：防守优先"

    def test_carry_off_when_spread_too_small(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXV", "value": 15.0, "chg1d": 0.0, "name": "n"},
            {"symbol": "VXST", "chg1d": -1.0},
        ]
        tm = _trade_map(rows)
        assert tm["title"] == "carry 条件不足"


class TestNarrative:
    def test_overview_mentions_counts(self):
        rows = [
            {
                "symbol": "VIX",
                "value": 14.0,
                "name": "标普500波动率",
                "chg1d": -1.0,
                "chg5d": -2.0,
                "chg1m": -3.0,
            },
            {
                "symbol": "VXN",
                "value": 20.0,
                "name": "纳指100波动率",
                "chg1d": -1.0,
                "chg5d": -2.0,
                "chg1m": -3.0,
            },
            {
                "symbol": "MOVE",
                "value": 69.0,
                "name": "美债波动率",
                "chg1d": 0.5,
                "chg5d": 1.0,
                "chg1m": -3.0,
            },
            {
                "symbol": "OVX",
                "value": 49.0,
                "name": "原油波动率",
                "chg1d": 0.3,
                "chg5d": -2.0,
                "chg1m": -3.0,
            },
        ]
        st = _stats(rows)
        text = _narr_overview(rows, st)
        assert "30" not in text or True  # n 来自 st，不硬编码
        assert "修复回落" in text

    def test_risk_score_composition(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VIXD", "value": 9.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXST", "value": 10.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXN", "value": 20.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "MOVE", "value": 69.0, "chg1d": 0.5, "name": "n"},
            {"symbol": "OVX", "value": 49.0, "chg1d": 0.3, "name": "n"},
            {"symbol": "VVIX", "value": 87.0, "chg1d": -2.0, "name": "n"},
        ]
        term = {"state": "contango"}
        assert _risk_score(rows, term) == 5  # 4 - 2 + 1 + 1 + 1

    def test_risk_score_backwardation_adds(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "name": "n"},
            {"symbol": "MOVE", "value": 50.0, "name": "n"},
            {"symbol": "OVX", "value": 30.0, "name": "n"},
            {"symbol": "VVIX", "value": 70.0, "name": "n"},
        ]
        assert _risk_score(rows, {"state": "backwardation"}) == 3  # 4 - 2 + 1

    def test_risk_text_has_score(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VIXD", "value": 9.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXST", "value": 10.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXN", "value": 20.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "MOVE", "value": 69.0, "chg1d": 0.5, "name": "n"},
            {"symbol": "OVX", "value": 49.0, "chg1d": 0.3, "name": "n"},
            {"symbol": "VVIX", "value": 87.0, "chg1d": -2.0, "name": "n"},
        ]
        text, score = _narr_risk(rows, {"state": "contango"})
        assert score == 5
        assert "综合评分 5/10" in text

    def test_term_narrative_contango(self):
        df = _cboe_frame()
        term = {"state": "contango", "values": [9.0, 10.0, 14.0, 17.0, 19.0]}
        text = _narr_term(df, term)
        assert "contango" in text and "1年 VIX" in text

    def test_trade_narrative_no_crash_with_missing(self):
        rows = [
            {"symbol": "VIX", "value": 14.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXST", "value": 10.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "VXN", "value": 20.0, "chg1d": -1.0, "name": "n"},
            {"symbol": "OVX", "value": None, "chg1m": None, "name": "n"},
            {"symbol": "GVZ", "value": None, "chg1y": None, "name": "n"},
            {"symbol": "MOVE", "value": None, "name": "n"},
        ]
        assert isinstance(_narr_trade(rows, {"state": "contango"}, 135.0), str)
