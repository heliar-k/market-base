"""detect_cdl_hits() 蜡烛形态可回看的 TDD 测试。

行为优先级：
6. detect_cdl_hits(df, as_of) 返回 as_of 那天命中的形态（非最后一行）
7. detect_cdl_hits(df) 无 as_of 与原 add_cdl_patterns 存 attrs 的行为一致
8. add_cdl_patterns 改造后 attrs 仍被正确设置
"""

import pandas as pd
import pytest

from src.analyze import analyze
from src.indicators import compute_all_indicators, detect_cdl_hits

# ═══ 行为 7：无 as_of 与原 attrs 行为一致 ═══


def test_detect_cdl_hits_without_as_of_matches_attrs(real_aapl_csv):
    """无 as_of 时 detect_cdl_hits 返回值与 add_cdl_patterns 存的 attrs 一致。"""
    if real_aapl_csv is None:
        pytest.skip("需要 data/stocks/AAPL.csv")
    df = (
        pd.read_csv(real_aapl_csv, parse_dates=["date"])
        .sort_values("date")
        .set_index("date")
    )
    df.columns = df.columns.str.lower()
    df = compute_all_indicators(df)

    bull, bear = detect_cdl_hits(df)
    assert bull == df.attrs["cdl_bullish"]
    assert bear == df.attrs["cdl_bearish"]


# ═══ 行为 8：add_cdl_patterns 改造后 attrs 仍被设置 ═══


def test_add_cdl_patterns_still_sets_attrs(sample_ohlcv_df):
    """改造后 add_cdl_patterns 仍把命中写入 attrs（原行为不破）。"""
    df = compute_all_indicators(sample_ohlcv_df.copy())
    # attrs 应被设置（哪怕是空列表）
    assert "cdl_bullish" in df.attrs
    assert "cdl_bearish" in df.attrs
    assert isinstance(df.attrs["cdl_bullish"], list)
    assert isinstance(df.attrs["cdl_bearish"], list)
    # 与直接调 detect_cdl_hits 一致
    bull, bear = detect_cdl_hits(df)
    assert df.attrs["cdl_bullish"] == bull
    assert df.attrs["cdl_bearish"] == bear


# ═══ 行为 6：as_of 决定命中行 ═══


def test_detect_cdl_hits_with_as_of_uses_that_row(real_aapl_csv):
    """as_of 那天的命中应被返回，即使最后一行不命中该形态。

    AAPL 2016-10-21 命中 CDL_HAMMER（锤子线），而最后一行（2026-06-26）不命中
    任何关注形态。这是验证回看正确性的黄金场景。
    """
    if real_aapl_csv is None:
        pytest.skip("需要 data/stocks/AAPL.csv")
    df = (
        pd.read_csv(real_aapl_csv, parse_dates=["date"])
        .sort_values("date")
        .set_index("date")
    )
    df.columns = df.columns.str.lower()
    df = compute_all_indicators(df)

    as_of = pd.Timestamp("2016-10-21")
    bull, bear = detect_cdl_hits(df, as_of=as_of)
    assert "锤子线" in bull  # 2016-10-21 命中锤子线
    assert bear == []  # 当天无看空形态

    # 无 as_of（最后一行）应不命中任何形态 —— 证明 as_of 真的换了视角
    bull_last, _ = detect_cdl_hits(df)
    assert "锤子线" not in bull_last


def test_analyze_as_of_wires_cdl_hits(real_aapl_csv):
    """analyze(as_of=...) 返回的 cdl_bullish 应是那天的命中（端到端验证）。"""
    if real_aapl_csv is None:
        pytest.skip("需要 data/stocks/AAPL.csv")
    df = (
        pd.read_csv(real_aapl_csv, parse_dates=["date"])
        .sort_values("date")
        .set_index("date")
    )
    df.columns = df.columns.str.lower()
    df = compute_all_indicators(df)

    result = analyze(df, "AAPL", as_of="2016-10-21")
    assert "锤子线" in result["cdl_bullish"]
    # 最后一行不含该形态
    full = analyze(df, "AAPL")
    assert "锤子线" not in full["cdl_bullish"]
