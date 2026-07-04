"""analyze() 可回看改造的 TDD 测试。

行为优先级（vertical slices）：
1. 无 as_of → 与重构前一致（用最后一行）
2. 有 as_of → last_date 指向 as_of
3. 有 as_of → ma_signals/ma_values 反映 as_of 那天
4. 有 as_of → changes 反映截至 as_of 的阶段涨跌
5. 有 as_of → resistance/support 是 as_of 往前 90 天
"""

import pandas as pd
import pytest

from src.analyze import analyze
from src.indicators import compute_all_indicators

# ═══ 共享：带指标的合成 df ═══


@pytest.fixture
def ind_df(sample_ohlcv_df):
    """对合成 OHLCV 跑完整指标，返回带全部列的 df。"""
    return compute_all_indicators(sample_ohlcv_df.copy())


# ═══ 行为 1：无 as_of 兼容现有行为（tracer bullet） ═══


def test_analyze_without_as_of_uses_last_row(ind_df):
    """无 as_of 时行为与重构前一致：symbol 透传、last_date 是最后一行。"""
    result = analyze(ind_df, "TEST")
    assert result["symbol"] == "TEST"
    assert result["last_date"] == str(ind_df.index[-1].date())


# ═══ 行为 2：as_of 决定 last_date ═══


def test_analyze_with_as_of_returns_that_date(ind_df):
    """传 as_of 时 last_date 是 as_of 那天，不是最后一行。"""
    as_of = ind_df.index[7]  # 第 8 行
    result = analyze(ind_df, "TEST", as_of=as_of)
    assert result["last_date"] == str(as_of.date())
    assert result["last_date"] != str(ind_df.index[-1].date())


# ═══ 行为 3：as_of 决定均线值/方向 ═══


def test_analyze_with_as_of_reflects_ma_at_that_date(ind_df):
    """as_of 时的 ma_values/ma_signals 反映那一天的均线，不是最后一行。"""
    as_of = ind_df.index[7]
    row = ind_df.loc[as_of]
    result = analyze(ind_df, "TEST", as_of=as_of)
    # MA5 在第 8 行应已有效（length>=5）
    if pd.notna(row.get("MA5")):
        assert result["ma_values"]["MA5"] == round(float(row["MA5"]), 2)
        expected_sig = "above" if row["close"] > row["MA5"] else "below"
        assert result["ma_signals"]["MA5"] == expected_sig
    # 且与最后一行的值不同（证明不是错拿末行）
    last_row = ind_df.iloc[-1]
    if pd.notna(row.get("MA5")) and pd.notna(last_row.get("MA5")):
        assert result["ma_values"]["MA5"] != round(float(last_row["MA5"]), 2)


# ═══ 行为 5：as_of 决定 90 日高低点 ═══


def test_analyze_with_as_of_resistance_support(ind_df):
    """as_of 时 resistance/support 是截至那天的窗口高低点。"""
    as_of = ind_df.index[10]
    result = analyze(ind_df, "TEST", as_of=as_of)
    window = ind_df.loc[:as_of].tail(90)
    assert result["resistance_90d"] == float(window["high"].max())
    assert result["support_90d"] == float(window["low"].min())
    # 且与全量末行视角不同（证明窗口真的以 as_of 为终点）
    full = analyze(ind_df, "TEST")
    assert result["resistance_90d"] != full["resistance_90d"]


# ═══ 行为 4：as_of 决定阶段涨跌 ═══


def test_analyze_with_as_of_reflects_changes_up_to_that_date(ind_df):
    """as_of 时 changes 是截至那天的阶段涨跌，基准是 as_of 前 N 天的 close。"""
    as_of = ind_df.index[10]  # 第 11 行，足够算 5d
    result = analyze(ind_df, "TEST", as_of=as_of)
    close_asof = float(ind_df.loc[as_of, "close"])
    # 5d = (as_of close - 5 天前 close) / 5 天前 close * 100
    close_5d_ago = float(ind_df["close"].loc[:as_of].iloc[-5])
    expected = round((close_asof - close_5d_ago) / close_5d_ago * 100, 2)
    assert result["changes"]["5d"] == expected
    # 且与“全量末行”视角的 5d 不同（证明真的截断了）
    full_result = analyze(ind_df, "TEST")
    assert result["changes"]["5d"] != full_result["changes"]["5d"]
