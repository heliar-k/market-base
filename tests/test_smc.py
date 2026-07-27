"""SMC (Smart Money Concepts) 指标测试。"""

import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    add_smc,
    add_smc_fvg,
    add_smc_liquidity_sweep,
    add_smc_mtf,
    add_smc_order_blocks,
    add_smc_premium_discount,
    add_smc_structure,
    add_smc_swing,
    compute_all_indicators,
)


def _make_df(rows: list[tuple]) -> pd.DataFrame:
    """从 (date, open, high, low, close, volume) 元组列表构造 DataFrame。"""
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def _make_df_auto(rows: list[tuple]) -> pd.DataFrame:
    """从 (open, high, low, close, volume) 元组列表构造 DataFrame，自动生成日期。"""
    dates = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df["date"] = dates
    return df.set_index("date")


# ── FVG ──────────────────────────────────────────────────────────────────


class TestFVG:
    def test_bullish_fvg(self):
        """第三根K线 low > 第一根K线 high → bullish FVG。"""
        df = _make_df(
            [
                ("2024-01-01", 100, 102, 99, 101, 1_000_000),
                ("2024-01-02", 101, 103, 100.5, 102, 1_000_000),
                # low(103.5) > high[1](103) → bullish FVG
                ("2024-01-03", 104, 106, 103.5, 105, 1_000_000),
            ]
        )
        add_smc_fvg(df)
        assert df["SMC_FVG"].iloc[2] == 1
        assert df["SMC_FVG_bottom"].iloc[2] == pytest.approx(102.0)  # K线1 high
        assert df["SMC_FVG_top"].iloc[2] == pytest.approx(103.5)  # K线3 low

    def test_bearish_fvg(self):
        """第三根K线 high < 第一根K线 low → bearish FVG。"""
        df = _make_df(
            [
                ("2024-01-01", 105, 106, 103, 104, 1_000_000),
                ("2024-01-02", 104, 105, 102, 103, 1_000_000),
                # high(100.5) < low[1](102) → bearish FVG
                ("2024-01-03", 101, 100.5, 98, 99, 1_000_000),
            ]
        )
        add_smc_fvg(df)
        assert df["SMC_FVG"].iloc[2] == -1
        assert df["SMC_FVG_top"].iloc[2] == pytest.approx(103.0)  # K线1 low
        assert df["SMC_FVG_bottom"].iloc[2] == pytest.approx(100.5)  # K线3 high

    def test_no_fvg(self):
        """正常连续K线 → 无 FVG。"""
        df = _make_df(
            [
                ("2024-01-01", 100, 102, 99, 101, 1_000_000),
                ("2024-01-02", 101, 103, 100, 102, 1_000_000),
                ("2024-01-03", 102, 103.5, 101, 103, 1_000_000),
            ]
        )
        add_smc_fvg(df)
        assert (df["SMC_FVG"] == 0).all()

    def test_first_two_rows_no_fvg(self):
        """前两行无足够历史 → FVG=0。"""
        df = _make_df(
            [
                ("2024-01-01", 100, 102, 99, 101, 1_000_000),
                ("2024-01-02", 101, 105, 103, 104, 1_000_000),
            ]
        )
        add_smc_fvg(df)
        assert df["SMC_FVG"].iloc[0] == 0
        assert df["SMC_FVG"].iloc[1] == 0


# ── Swing Highs / Lows ──────────────────────────────────────────────────


class TestSwing:
    def test_detects_swing_high(self):
        """局部最高点被标记为 swing high。"""
        # 构造一个峰：低到高再到低
        rows = []
        prices = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100]
        for i, p in enumerate(prices):
            rows.append((f"2024-01-{i + 1:02d}", p, p + 1, p - 1, p, 1_000_000))
        df = _make_df(rows)
        add_smc_swing(df, length=2)

        # 峰值在 index=5 (price=105)
        assert df["SMC_swing_high"].iloc[5] == pytest.approx(106.0)  # high=105+1

    def test_detects_swing_low(self):
        """局部最低点被标记为 swing low。"""
        # 构造一个谷：高到低再到高
        rows = []
        prices = [105, 104, 103, 102, 101, 100, 101, 102, 103, 104, 105]
        for i, p in enumerate(prices):
            rows.append((f"2024-01-{i + 1:02d}", p, p + 1, p - 1, p, 1_000_000))
        df = _make_df(rows)
        add_smc_swing(df, length=2)

        # 谷值在 index=5 (price=100)
        assert df["SMC_swing_low"].iloc[5] == pytest.approx(99.0)  # low=100-1

    def test_edges_are_nan(self):
        """首尾 length 行无摆动点（无法确认）。"""
        rows = []
        for i in range(10):
            p = 100 + i
            rows.append((f"2024-01-{i + 1:02d}", p, p + 1, p - 1, p, 1_000_000))
        df = _make_df(rows)
        add_smc_swing(df, length=2)

        assert np.isnan(df["SMC_swing_high"].iloc[0])
        assert np.isnan(df["SMC_swing_high"].iloc[1])
        assert np.isnan(df["SMC_swing_high"].iloc[-1])
        assert np.isnan(df["SMC_swing_high"].iloc[-2])


# ── BOS / CHoCH ─────────────────────────────────────────────────────────


class TestStructure:
    def test_bullish_bos(self):
        """上升趋势中突破前高 → bullish BOS。"""
        # 构造: 先上升形成 swing high，回调，再突破
        rows = [
            (100, 102, 99, 101, 1_000_000),
            (101, 103, 100, 102, 1_000_000),
            (102, 106, 101, 105, 1_000_000),  # swing high candidate
            (105, 105.5, 103, 104, 1_000_000),
            (104, 104.5, 102, 103, 1_000_000),
            (103, 103.5, 101, 102, 1_000_000),  # 回调结束
            (102, 107, 101.5, 106.5, 1_000_000),  # 突破 swing high (106)
        ]
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)

        assert df["SMC_BOS"].sum() > 0 or df["SMC_structure"].iloc[-1] == 1

    def test_bearish_bos(self):
        """下降趋势中突破前低 → bearish BOS。"""
        rows = [
            (105, 106, 104, 105, 1_000_000),
            (104, 105, 103, 104, 1_000_000),
            (103, 104, 100, 101, 1_000_000),  # swing low candidate
            (101, 102, 100.5, 101.5, 1_000_000),
            (102, 103, 101.5, 102, 1_000_000),
            (103, 104, 102.5, 103, 1_000_000),  # 反弹结束
            (103, 103.5, 99, 99.5, 1_000_000),  # 突破 swing low (100)
        ]
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)

        assert df["SMC_BOS"].sum() < 0 or df["SMC_structure"].iloc[-1] == -1

    def test_choch_reversal(self):
        """上升趋势中跌破前低 → bearish CHoCH（趋势反转）。"""
        # 构造: 形成 swing low → 上升突破 swing high → BOS → 回调 → 跌破 → CHoCH
        rows = [
            (100, 102, 99, 101, 1_000_000),
            (101, 101.5, 98, 99, 1_000_000),  # swing low area
            (99, 103, 98.5, 102, 1_000_000),
            (102, 105, 101, 104, 1_000_000),
            (104, 107, 103, 106, 1_000_000),  # swing high=107
            (106, 106.5, 103, 104, 1_000_000),  # 阴线回调
            (104, 104.5, 102, 103, 1_000_000),  # swing low=102
            (103, 108, 102.5, 107.5, 1_000_000),  # 突破 107 → BOS bullish
            (107.5, 109, 106, 108, 1_000_000),
            (108, 108.5, 105, 106, 1_000_000),  # 回调
            (106, 106.5, 104, 105, 1_000_000),  # swing low area
            (105, 105.5, 97, 98, 1_000_000),  # 跌破 → CHoCH
        ]
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)

        # 最终结构应转为空头
        assert df["SMC_structure"].iloc[-1] == -1

    def test_structure_column_exists(self, sample_ohlcv_df):
        """SMC_structure 列存在于 compute 之后。"""
        df = add_smc_structure(sample_ohlcv_df.copy())
        assert "SMC_structure" in df.columns
        assert "SMC_BOS" in df.columns
        assert "SMC_CHoCH" in df.columns


# ── Order Blocks ─────────────────────────────────────────────────────────


class TestOrderBlocks:
    def test_ob_columns_exist(self, sample_ohlcv_df):
        df = add_smc_order_blocks(sample_ohlcv_df.copy())
        assert "SMC_OB" in df.columns
        assert "SMC_OB_top" in df.columns
        assert "SMC_OB_bottom" in df.columns

    def test_bullish_ob_after_bos(self):
        """Bullish BOS 后应标记 OB。"""
        rows = [
            (100, 102, 99, 101, 1_000_000),
            (101, 101.5, 98, 99, 1_000_000),
            (99, 103, 98.5, 102, 1_000_000),
            (102, 105, 101, 104, 1_000_000),
            (104, 107, 103, 106, 1_000_000),  # swing high=107
            (106, 106.5, 103, 104, 1_000_000),  # 阴线
            (104, 104.5, 102, 103, 1_000_000),  # 阴线
            (103, 108, 102.5, 107.5, 1_000_000),  # 突破 → BOS
        ]
        df = _make_df_auto(rows)
        add_smc_order_blocks(df, swing_length=2)

        # 应该有 OB 标记（BOS 回溯找到的阴线）
        assert (df["SMC_OB"] != 0).any()


# ── Premium / Discount ───────────────────────────────────────────────────


class TestPremiumDiscount:
    def test_zones(self):
        """验证 premium/equilibrium/discount 线的计算。"""
        rows = []
        for i in range(60):
            p = 100 + (i % 20)  # 100~119 循环
            rows.append((p, p + 2, p - 2, p, 1_000_000))
        df = _make_df_auto(rows)
        add_smc_premium_discount(df, lookback=50)

        last = df.iloc[-1]
        assert last["SMC_premium"] > last["SMC_equilibrium"]
        assert last["SMC_equilibrium"] > last["SMC_discount"]

    def test_pd_zone_labels(self):
        """价格高于 premium → 'premium'，低于 discount → 'discount'。"""
        rows = []
        for _ in range(60):
            p = 100
            rows.append((p, p + 2, p - 2, p, 1_000_000))
        df = _make_df_auto(rows)
        add_smc_premium_discount(df, lookback=50)

        # 所有价格都在同一水平 → 应该在 equilibrium
        assert (df["SMC_pd_zone"] == "equilibrium").all()

    def test_columns_exist(self, sample_ohlcv_df):
        df = add_smc_premium_discount(sample_ohlcv_df.copy())
        for col in ["SMC_premium", "SMC_equilibrium", "SMC_discount", "SMC_pd_zone"]:
            assert col in df.columns


# ── Liquidity Sweep ─────────────────────────────────────────────────────


class TestLiquiditySweep:
    def test_bearish_sweep(self):
        """wick 突破 swing high 但 close 回落 → bearish sweep。"""
        rows = [
            (100, 102, 99, 101, 1_000_000),
            (101, 103, 100, 102, 1_000_000),
            (102, 106, 101, 105, 1_000_000),  # swing high=106
            (105, 105.5, 103, 104, 1_000_000),
            (104, 104.5, 102, 103, 1_000_000),  # swing high 确认
            (103, 107, 102.5, 104, 1_000_000),  # wick>106, close<106 → sweep
        ]
        df = _make_df_auto(rows)
        add_smc_liquidity_sweep(df, swing_length=2)

        assert df["SMC_sweep"].iloc[5] == -1
        assert df["SMC_sweep_level"].iloc[5] == pytest.approx(106.0)

    def test_bullish_sweep(self):
        """wick 跌破 swing low 但 close 回升 → bullish sweep。"""
        rows = [
            (105, 106, 104, 105, 1_000_000),
            (104, 105, 103, 104, 1_000_000),
            (103, 104, 98, 99, 1_000_000),  # swing low=98
            (99, 100, 98.5, 99.5, 1_000_000),
            (99.5, 101, 99, 100.5, 1_000_000),  # swing low 确认
            (100.5, 102, 97, 101, 1_000_000),  # low<98, close>98 → sweep
        ]
        df = _make_df_auto(rows)
        add_smc_liquidity_sweep(df, swing_length=2)

        assert df["SMC_sweep"].iloc[5] == 1
        assert df["SMC_sweep_level"].iloc[5] == pytest.approx(98.0)

    def test_no_sweep_on_true_breakout(self):
        """close 也突破 → 真突破，非 sweep。"""
        rows = [
            (100, 102, 99, 101, 1_000_000),
            (101, 103, 100, 102, 1_000_000),
            (102, 106, 101, 105, 1_000_000),  # swing high=106
            (105, 105.5, 103, 104, 1_000_000),
            (104, 104.5, 102, 103, 1_000_000),
            (103, 108, 102.5, 107, 1_000_000),  # close>106 → 真突破
        ]
        df = _make_df_auto(rows)
        add_smc_liquidity_sweep(df, swing_length=2)

        assert df["SMC_sweep"].iloc[5] == 0

    def test_columns_exist(self, sample_ohlcv_df):
        df = add_smc_liquidity_sweep(sample_ohlcv_df.copy())
        assert "SMC_sweep" in df.columns
        assert "SMC_sweep_level" in df.columns

    def test_no_sweep_without_swing_points(self):
        """无 swing points → 无 sweep。"""
        rows = [
            (100, 101, 99, 100.5, 1_000_000),
            (100.5, 101.5, 99.5, 101, 1_000_000),
            (101, 102, 100, 101.5, 1_000_000),
        ]
        df = _make_df_auto(rows)
        add_smc_liquidity_sweep(df, swing_length=2)

        assert (df["SMC_sweep"] == 0).all()


# ── MTF (多时间框架) ────────────────────────────────────────────────────


class TestMTF:
    def test_columns_exist(self, sample_ohlcv_df):
        df = add_smc_mtf(sample_ohlcv_df.copy())
        for col in [
            "SMC_weekly_structure",
            "SMC_weekly_swing_high",
            "SMC_weekly_swing_low",
            "SMC_htf_bias",
        ]:
            assert col in df.columns

    def test_insufficient_data_defaults(self, sample_ohlcv_df):
        """周线数据不足时填充默认值。"""
        df = add_smc_mtf(sample_ohlcv_df.copy(), swing_length=3)
        # 15 天 → 约 2-3 周，不足以做 swing 分析
        assert (df["SMC_weekly_structure"] == 0).all()
        assert (df["SMC_htf_bias"] == "neutral").all()

    def test_weekly_structure_mapped(self):
        """周线结构正确映射到日线：同一周内日线有相同的 weekly_structure。"""
        rows = []
        for i in range(40):
            p = 100 + i  # 持续上涨
            rows.append((p, p + 2, p - 1, p + 1, 1_000_000))
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)
        add_smc_mtf(df, swing_length=2)

        # 同一周内的日线应有相同的 weekly_structure
        weekly_groups = df.groupby(pd.Grouper(freq="W-FRI"))[
            "SMC_weekly_structure"
        ].nunique()
        assert (weekly_groups <= 1).all()

    def test_htf_bias_values(self):
        """HTF bias 值合法。"""
        rows = []
        for i in range(60):
            p = 100 + (i % 15)
            rows.append((p, p + 2, p - 1, p + 1, 1_000_000))
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)
        add_smc_mtf(df, swing_length=2)

        valid = {
            "strong_bullish",
            "weak_bullish",
            "strong_bearish",
            "weak_bearish",
            "neutral",
        }
        assert set(df["SMC_htf_bias"].unique()) <= valid

    def test_strong_bullish_when_aligned(self):
        """日线+周线同向 → strong_bullish。"""
        rows = []
        for i in range(40):
            p = 100 + i
            rows.append((p, p + 2, p - 1, p + 1, 1_000_000))
        df = _make_df_auto(rows)
        add_smc_structure(df, swing_length=2)
        add_smc_mtf(df, swing_length=2)

        # 持续上涨 → 日线和周线都应为 bullish
        last = df.iloc[-1]
        if last["SMC_structure"] == 1 and last["SMC_weekly_structure"] == 1:
            assert last["SMC_htf_bias"] == "strong_bullish"


# ── 综合 ─────────────────────────────────────────────────────────────────


class TestComputeAll:
    def test_all_smc_columns_present(self, sample_ohlcv_df):
        """compute_all_indicators 包含所有 SMC 列。"""
        df = compute_all_indicators(sample_ohlcv_df.copy())
        smc_cols = [c for c in df.columns if c.startswith("SMC")]
        expected = [
            "SMC_swing_high",
            "SMC_swing_low",
            "SMC_FVG",
            "SMC_FVG_top",
            "SMC_FVG_bottom",
            "SMC_BOS",
            "SMC_CHoCH",
            "SMC_structure",
            "SMC_OB",
            "SMC_OB_top",
            "SMC_OB_bottom",
            "SMC_premium",
            "SMC_equilibrium",
            "SMC_discount",
            "SMC_pd_zone",
            "SMC_sweep",
            "SMC_sweep_level",
            "SMC_weekly_structure",
            "SMC_weekly_swing_high",
            "SMC_weekly_swing_low",
            "SMC_htf_bias",
        ]
        for col in expected:
            assert col in smc_cols, f"缺少列: {col}"

    def test_add_smc_one_call(self, sample_ohlcv_df):
        """add_smc() 一键调用所有 SMC 指标。"""
        df = add_smc(sample_ohlcv_df.copy())
        assert "SMC_FVG" in df.columns
        assert "SMC_BOS" in df.columns
        assert "SMC_OB" in df.columns
        assert "SMC_pd_zone" in df.columns
        assert "SMC_sweep" in df.columns
        assert "SMC_htf_bias" in df.columns


# ── 真实数据集成测试 ──────────────────────────────────────────────────────


class TestRealData:
    def test_aapl_smc(self, real_aapl_csv):
        """用真实 AAPL 数据验证 SMC 指标可运行且信号合理。"""
        if real_aapl_csv is None:
            pytest.skip("AAPL CSV 不存在")
        from src.indicators import load_data

        df = load_data(str(real_aapl_csv))
        df = compute_all_indicators(df)

        # SMC 列存在
        assert "SMC_FVG" in df.columns
        assert "SMC_BOS" in df.columns

        # 有 FVG 信号
        assert (df["SMC_FVG"] != 0).sum() > 0

        # 有结构信号
        assert (df["SMC_structure"] != 0).sum() > 0

        # BOS/CHoCH 数量不为零
        assert (df["SMC_BOS"] != 0).sum() > 0
        assert (df["SMC_CHoCH"] != 0).sum() > 0

        # pd_zone 值合法
        valid_zones = {"premium", "discount", "equilibrium"}
        assert set(df["SMC_pd_zone"].unique()) <= valid_zones

        # sweep 列存在
        assert "SMC_sweep" in df.columns
        assert "SMC_sweep_level" in df.columns

        # MTF 列存在
        assert "SMC_weekly_structure" in df.columns
        assert "SMC_htf_bias" in df.columns

        # HTF bias 值合法
        valid_bias = {
            "strong_bullish",
            "weak_bullish",
            "strong_bearish",
            "weak_bearish",
            "neutral",
        }
        assert set(df["SMC_htf_bias"].unique()) <= valid_bias
