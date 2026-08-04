"""volatility_analysis 规则引擎单元测试（区间判定 / 变动计算 / 期限结构）。"""

import pandas as pd
import pytest

from src import volatility_analysis as va
from src.volatility_analysis import (
    _chg_pct,
    _percentile,
    _zone,
    signal_outlook,
    signal_term,
    signal_vix_level,
    term_structure,
    vix_card,
    vix_history,
)


class TestZone:
    def test_zones(self):
        assert _zone(12.0)[0] == "平静"
        assert _zone(15.0)[0] == "正常"
        assert _zone(30.0)[0] == "警戒"
        assert _zone(40.0)[0] == "恐慌"

    def test_boundary(self):
        # 15 恰好落入正常区（含下界）
        assert _zone(15.0)[0] == "正常"


class TestChgPct:
    def test_round_trip(self):
        s = pd.Series([100.0, 110.0, 99.0, 90.0])
        assert _chg_pct(s, 1) == pytest.approx(-9.09)
        assert _chg_pct(s, 2) == pytest.approx(-18.18)

    def test_insufficient(self):
        assert _chg_pct(pd.Series([100.0]), 3) is None


class TestPercentile:
    def test_current_rank(self):
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        assert _percentile(s) == 100.0
        assert _percentile(pd.Series([50.0, 40.0, 30.0, 20.0, 10.0])) == 20.0


class TestTermStructure:
    def test_build(self):
        df = pd.DataFrame(
            {
                "VIX1D": [9.0, 9.43],
                "VIX9D": [13.0, 13.28],
                "VIX": [16.0, 15.86],
                "VIX3M": [19.0, 18.93],
                "VIX6M": [21.0, 21.2],
                "VIX_TERM_SLOPE": [3.0, 2.58],
            },
            index=pd.to_datetime(["2026-07-31", "2026-08-03"]),
        )
        ts = term_structure(df)
        assert ts["values"] == [9.43, 13.28, 15.86, 18.93, 21.2]
        assert ts["state"] == "contango"
        assert ts["slope"] == pytest.approx(2.58)


# 构造 6 行样本供信号函数使用（_chg_pct 至少需要 6 行算周变动）
@pytest.fixture
def sig_df():
    idx = pd.to_datetime(
        [
            "2026-07-27",
            "2026-07-28",
            "2026-07-29",
            "2026-07-30",
            "2026-07-31",
            "2026-08-03",
        ]
    )
    return pd.DataFrame(
        {
            "VIX1D": [10.0, 12.0, 11.0, 13.0, 12.29, 9.43],
            "VIX9D": [14.0, 15.0, 16.0, 17.0, 13.05, 13.28],
            "VIX": [19.0, 20.0, 21.0, 18.0, 15.99, 15.86],
            "VIX3M": [21.0, 21.5, 21.8, 21.0, 19.02, 18.93],
            "VIX6M": [22.0, 22.5, 22.8, 22.0, 21.34, 21.2],
            "VIX1Y": [23.0, 23.5, 23.8, 23.0, 22.94, 22.9],
            "SKEW": [140.0, 141.0, 142.0, 141.5, 141.23, 139.96],
            "OVX": [60.0, 62.0, 63.0, 64.0, 63.04, 57.2],
            "VIX_TERM_SLOPE": [5.0, 5.0, 5.0, 1.0, 2.94, 2.58],
        },
        index=idx,
    )


class TestSignals:
    def test_signal_vix_level(self, sig_df):
        card = vix_card(sig_df)
        text = signal_vix_level(card)
        assert "15.86" in text and "正常区间" in text and "周跌" in text

    def test_signal_term_contango(self, sig_df):
        text = signal_term(term_structure(sig_df))
        assert "contango" in text and "9.43" in text and "21.2" in text

    def test_signal_term_no_slope(self, sig_df):
        # VIX_TERM_SLOPE 缺失时 state 为 "—"，不误判 backwardation
        term = term_structure(sig_df.drop(columns=["VIX_TERM_SLOPE"]))
        assert term["state"] == "—"
        assert "暂不判断" in signal_term(term)

    def test_signal_outlook_none_chg(self, sig_df):
        # VIX9D 不足 6 行时周变动为 None，不应输出自相矛盾的方向断言
        short = sig_df.tail(3)
        text = signal_outlook(short, vix_card(short))
        assert "近一周下行" not in text and "近一周上行" not in text

    def test_signal_outlook_normal(self, sig_df):
        text = signal_outlook(sig_df, vix_card(sig_df))
        assert "未来一周 VIX" in text and "OVX" in text and "SKEW" in text

    def test_signal_outlook_low_skew(self, sig_df):
        # SKEW < 130 应有独立分支，不硬编码 "130-140"
        low = sig_df.copy()
        low["SKEW"] = 120.0
        assert "低于 130" in signal_outlook(low, vix_card(low))


class TestVixHistory:
    def test_skew_aligned_to_vix_dates(self, sig_df):
        hist = vix_history(sig_df, days=10)
        assert len(hist["dates"]) == len(hist["vix"]) == len(hist["skew"])
        # SKEW 缺失时补 None，不丢对齐
        assert hist["skew"][-1] == pytest.approx(139.96)

    def test_generate_entry(self, sig_df, monkeypatch):
        # 入口契约：SKEW 为硬依赖，输出字段前后端一一对应
        monkeypatch.setattr(va, "_read", lambda: sig_df)
        out = va.generate_volatility_analysis()
        assert out["generator"] == "rules"
        assert out["as_of"] == "2026-08-03"
        assert len(out["signals"]) == 3
        h = out["vix_history"]
        assert len(h["dates"]) == len(h["vix"]) == len(h["skew"])
        # SKEW 子图复用 vix_history，不再单独传 skew_history
        assert "skew_history" not in out
        assert len(out["vix_skew_scatter"]) == len(sig_df)
        assert len(out["recent"]) == 6

    def test_generate_entry_missing_skew(self, sig_df, monkeypatch):
        monkeypatch.setattr(va, "_read", lambda: sig_df.drop(columns=["SKEW"]))
        out = va.generate_volatility_analysis()
        assert "error" in out
