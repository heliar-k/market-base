"""detect_cdl_hits() 蜡烛形态可回看的 TDD 测试。

行为优先级：
6. detect_cdl_hits(df, as_of) 返回 as_of 那天命中的形态（非最后一行）
"""

import pandas as pd
import pytest

from src.analyze import analyze
from src.indicators import compute_all_indicators, detect_cdl_hits

# ═══ 行为 6：as_of 决定命中行 ═══


def test_detect_cdl_hits_with_as_of_uses_that_row(real_aapl_csv):
    """as_of 那天的命中应被返回，即使最后一行不命中该形态。

    AAPL 2016-10-21 命中 CDL_HAMMER（锤子线），而末行（截断后固定为 2016-10-24）
    不命中任何看多形态。这是验证回看正确性的黄金场景。
    """
    if real_aapl_csv is None:
        pytest.skip("需要 data/stocks/AAPL.csv")
    df = (
        pd.read_csv(real_aapl_csv, parse_dates=["date"])
        .sort_values("date")
        .set_index("date")
    )
    # 截断到固定历史窗口：真实 CSV 每日自动追加，「最后一行」会随数据漂移
    # （曾漂移成锤子线日导致断言失败）。截断后末行恒为 2016-10-24（非锤子线），
    # CDL 形态无未来函数，2016-10-21 的命中不受影响。
    df = df.loc[:"2016-10-24"]
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
    # 截断到固定历史窗口（同上）：真实 CSV 每日追加会使「最后一行」漂移，
    # 截断后无 as_of 时取的末行恒为 2016-10-24，断言不再随数据漂移失败。
    df = df.loc[:"2016-10-24"]
    df.columns = df.columns.str.lower()
    df = compute_all_indicators(df)

    result = analyze(df, "AAPL", as_of="2016-10-21")
    assert "锤子线" in result["cdl_bullish"]
    # 末行（截断后固定为 2016-10-24）不含该形态
    full = analyze(df, "AAPL")
    assert "锤子线" not in full["cdl_bullish"]
