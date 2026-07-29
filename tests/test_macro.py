"""src/macro.py 的 TDD 测试。

测试 derive_macro() 对含原始 FRED 列的 df 追加派生宏观指标列。
合成数据，不依赖真实 CSV。
"""

import pandas as pd
import pytest

from src.macro import (
    DERIVED_INPUTS,
    cross_category_partners,
    cross_category_series_for,
    derive_macro,
    derived_series_for_category,
)


def test_derive_macro_empty_df_returns_unchanged():
    """tracer bullet：空 df（无 FRED 列）返回原 df 不变，不报错。"""
    df = pd.DataFrame(index=pd.date_range("2024-01-01", periods=3))
    out = derive_macro(df)
    assert list(out.columns) == []
    assert len(out) == 3


def test_derive_macro_2s10s_spread():
    """含 DGS10/DGS2 时生成 SPREAD_2S10S = DGS10 − DGS2（百分点）。"""
    df = pd.DataFrame(
        {"DGS10": [4.0, 4.1, 4.2], "DGS2": [3.5, 3.7, 3.9]},
        index=pd.date_range("2024-01-01", periods=3),
    )
    out = derive_macro(df)
    assert "SPREAD_2S10S" in out.columns
    pd.testing.assert_series_equal(
        out["SPREAD_2S10S"],
        pd.Series([0.5, 0.4, 0.3], index=df.index, name="SPREAD_2S10S"),
        check_names=False,
    )


@pytest.mark.parametrize(
    "cols,expected_derived",
    [
        # 2s10s：缺任一输入列则不生成
        (["DGS10"], "SPREAD_2S10S"),
        (["DGS2"], "SPREAD_2S10S"),
        # NET_LIQUIDITY：缺任一输入列则不生成
        (["WALCL", "RRPONTSYD"], "NET_LIQUIDITY"),
        (["WALCL", "WTREGEN"], "NET_LIQUIDITY"),
        (["RRPONTSYD", "WTREGEN"], "NET_LIQUIDITY"),
        # BEI_5Y：缺任一输入列则不生成
        (["DGS5"], "BEI_5Y"),
        (["DFII5"], "BEI_5Y"),
        # BEI_10Y：缺任一输入列则不生成
        (["DGS10"], "BEI_10Y"),
        (["DFII10"], "BEI_10Y"),
        # SOFR_IORB_SPREAD_BP：缺任一输入列则不生成
        (["SOFR"], "SOFR_IORB_SPREAD_BP"),
        (["IORB"], "SOFR_IORB_SPREAD_BP"),
    ],
)
def test_derive_macro_skips_when_input_missing(cols, expected_derived):
    """缺输入列时跳过对应派生（不报错、不追加该派生列）。"""
    df = pd.DataFrame(
        {c: [1.0, 2.0] for c in cols},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = derive_macro(df)
    assert expected_derived not in out.columns


def test_derive_macro_net_liquidity():
    """含 WALCL/RRPONTSYD/WTREGEN 时生成 NET_LIQUIDITY。

    NET_LIQUIDITY = WALCL − RRPONTSYD×1000 − WTREGEN（百万美元）。
    注意 RRPONTSYD 在 FRED 单位为十亿美元，这里用真实量级数据确保 ×1000 被执行
    （否则 RRP~1.4 billions 会被当百万误减，结果几乎不变、掩盖 bug）。
    """
    df = pd.DataFrame(
        {
            "WALCL": [6_747_378, 6_800_000],  # 联储总资产，百万美元
            "RRPONTSYD": [1.38, 2.0],  # 逆回购，十亿美元
            "WTREGEN": [829_623, 840_000],  # TGA，百万美元
        },
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = derive_macro(df)
    assert "NET_LIQUIDITY" in out.columns
    # WALCL - RRP×1000 - TGA (单位均百万美元)
    # 6_747_378 - 1.38*1000 - 829_623 = 5_916_375
    # 6_800_000 - 2*1000 - 840_000 = 5_958_000
    assert out["NET_LIQUIDITY"].tolist() == [5_916_375.0, 5_958_000.0]


def test_derive_macro_bei_5y():
    """含 DGS5/DFII5 时生成 BEI_5Y = (DGS5 − DFII5) × 100（bp）。"""
    df = pd.DataFrame(
        {"DGS5": [4.0, 4.2], "DFII5": [1.5, 1.6]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = derive_macro(df)
    assert "BEI_5Y" in out.columns
    pd.testing.assert_series_equal(
        out["BEI_5Y"],
        pd.Series([250.0, 260.0], index=df.index, name="BEI_5Y"),
        check_names=False,
    )


def test_derive_macro_bei_10y():
    """含 DGS10/DFII10 时生成 BEI_10Y = (DGS10 − DFII10) × 100（bp）。"""
    df = pd.DataFrame(
        {"DGS10": [4.5, 4.6], "DFII10": [1.8, 1.9]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = derive_macro(df)
    assert "BEI_10Y" in out.columns
    pd.testing.assert_series_equal(
        out["BEI_10Y"],
        pd.Series([270.0, 270.0], index=df.index, name="BEI_10Y"),
        check_names=False,
    )


def test_derive_macro_sofr_iorb_spread_bp():
    """含 SOFR/IORB 时生成 SOFR_IORB_SPREAD_BP = (SOFR − IORB) × 100（bp）。"""
    df = pd.DataFrame(
        {"SOFR": [5.3, 5.31], "IORB": [5.4, 5.4]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    out = derive_macro(df)
    assert "SOFR_IORB_SPREAD_BP" in out.columns
    assert out["SOFR_IORB_SPREAD_BP"].tolist() == pytest.approx([-10.0, -9.0])


def test_derive_macro_overwrites_existing_derived_column():
    """macro.py 是权威定义：已存在的派生列会被覆盖。"""
    df = pd.DataFrame(
        {"DGS10": [4.0], "DGS2": [3.5], "SPREAD_2S10S": [99.0]},  # 错误的预存值
        index=pd.date_range("2024-01-01", periods=1),
    )
    out = derive_macro(df)
    assert out["SPREAD_2S10S"].iloc[0] == 0.5


def test_derive_macro_all_inputs_all_derived():
    """综合：传入含全部原始列的 df，全部 5 个派生列生成且值正确。"""
    df = pd.DataFrame(
        {
            "DGS2": [3.5],
            "DGS5": [4.0],
            "DGS10": [4.5],
            "DFII5": [1.5],
            "DFII10": [1.8],
            "SOFR": [5.3],
            "IORB": [5.4],
            "WALCL": [6_747_378],
            "RRPONTSYD": [1.38],
            "WTREGEN": [829_623],
        },
        index=pd.date_range("2024-01-01", periods=1),
    )
    out = derive_macro(df)
    for col in [
        "SPREAD_2S10S",
        "NET_LIQUIDITY",
        "BEI_5Y",
        "BEI_10Y",
        "SOFR_IORB_SPREAD_BP",
    ]:
        assert col in out.columns
    assert out["SPREAD_2S10S"].iloc[0] == 1.0
    assert out["NET_LIQUIDITY"].iloc[0] == 5_916_375.0
    assert out["BEI_5Y"].iloc[0] == 250.0
    assert out["BEI_10Y"].iloc[0] == 270.0
    assert out["SOFR_IORB_SPREAD_BP"].iloc[0] == pytest.approx(-10.0)


# ──────────────────────────────────────────────────────────────────────
# derived_series_for_category：UI 用的派生系列归属查询
# ──────────────────────────────────────────────────────────────────────


def test_derived_series_for_rates_has_spread_and_sofr_iorb() -> None:
    """rates 分类含 DGS2/DGS10/SOFR/IORB → 派生 SPREAD_2S10S + SOFR_IORB_SPREAD_BP。"""
    out = derived_series_for_category("rates")
    assert "SPREAD_2S10S" in out
    assert "SOFR_IORB_SPREAD_BP" in out


def test_derived_series_for_liquidity_has_net_liquidity() -> None:
    """liquidity 分类含 WALCL/RRPONTSYD/WTREGEN → 派生 NET_LIQUIDITY。"""
    assert "NET_LIQUIDITY" in derived_series_for_category("liquidity")


def test_derived_series_for_tips_empty() -> None:
    """tips 只有 DFII 系列，无 DGS → 不生成任何派生（BEI 需跨分类）。"""
    assert derived_series_for_category("tips") == []


def test_derived_inputs_matches_derive_functions() -> None:
    """DERIVED_INPUTS 的键覆盖全部 5 个派生指标。"""
    assert set(DERIVED_INPUTS.keys()) == {
        "SPREAD_2S10S",
        "NET_LIQUIDITY",
        "BEI_5Y",
        "BEI_10Y",
        "SOFR_IORB_SPREAD_BP",
    }


# ──────────────────────────────────────────────────────────────────────
# cross_category_series_for / cross_category_partners：跨分类派生（BEI）归属查询
# BEI 需 rates 的 DGS + tips 的 DFII，横跨两个分类，故 rates 和 tips 都应列出 BEI。
# ──────────────────────────────────────────────────────────────────────


def test_cross_category_series_for_rates_has_bei() -> None:
    """rates 分类可参与跨分类派生 BEI_5Y/BEI_10Y（DGS5/DGS10 在 rates）。"""
    out = cross_category_series_for("rates")
    assert "BEI_5Y" in out
    assert "BEI_10Y" in out


def test_cross_category_series_for_tips_has_bei() -> None:
    """tips 分类同样可参与跨分类派生 BEI_5Y/BEI_10Y（DFII5/DFII10 在 tips）。"""
    out = cross_category_series_for("tips")
    assert "BEI_5Y" in out
    assert "BEI_10Y" in out


def test_cross_category_series_for_volatility_empty() -> None:
    """volatility 无跨分类派生 → 返回空。"""
    assert cross_category_series_for("volatility") == []


def test_cross_category_series_for_liquidity_empty() -> None:
    """liquidity 的 NET_LIQUIDITY 是单分类派生，不在跨分类返回。

    单分类派生已在 derived_series_for_category 返回。
    """
    assert cross_category_series_for("liquidity") == []


def test_cross_category_partners_rates_is_tips() -> None:
    """rates 的跨分类伙伴 = tips（合并 tips CSV 才能算 BEI）。"""
    assert cross_category_partners("rates") == ["tips"]


def test_cross_category_partners_tips_is_rates() -> None:
    """tips 的跨分类伙伴 = rates。"""
    assert cross_category_partners("tips") == ["rates"]


def test_cross_category_partners_volatility_empty() -> None:
    """volatility 无跨分类伙伴。"""
    assert cross_category_partners("volatility") == []


def test_merge_rates_tips_then_derive_has_bei() -> None:
    """合成 rates+tips df，按 date 合并后调 derive_macro，BEI 列生成且值正确。

    模拟宏观加载流程的合并步骤：主分类(rates) left join 伙伴(tips)。
    """
    idx = pd.date_range("2024-01-01", periods=2)
    rates = pd.DataFrame({"DGS5": [4.0, 4.2], "DGS10": [4.5, 4.6]}, index=idx)
    tips = pd.DataFrame({"DFII5": [1.5, 1.6], "DFII10": [1.8, 1.9]}, index=idx)
    merged = rates.join(tips, how="left")
    out = derive_macro(merged)
    assert "BEI_5Y" in out.columns
    assert "BEI_10Y" in out.columns
    assert out["BEI_5Y"].tolist() == [250.0, 260.0]
    assert out["BEI_10Y"].tolist() == [270.0, 270.0]


def test_merge_rates_tips_misaligned_dates_bei_nan_where_missing() -> None:
    """日期错位时，缺数据行的 BEI 为 NaN（不报错），有数据行正常计算。"""
    rates = pd.DataFrame(
        {"DGS5": [4.0, 4.2], "DGS10": [4.5, 4.6]},
        index=pd.date_range("2024-01-01", periods=2),
    )
    tips = pd.DataFrame(
        {"DFII5": [1.5], "DFII10": [1.8]},
        index=pd.to_datetime(["2024-01-01"]),
    )
    merged = rates.join(tips, how="left")
    out = derive_macro(merged)
    # 第 1 行两者齐备 → 有值；第 2 行缺 DFII → NaN
    assert out["BEI_5Y"].iloc[0] == 250.0
    assert pd.isna(out["BEI_5Y"].iloc[1])
