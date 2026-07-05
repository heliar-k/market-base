"""KlineChart 纯逻辑测试：visible_window 窗口滚动计算。

不依赖 Textual、不依赖 plotext、不依赖真实数据。用合成 df 驱动。
渲染层（PlotextPlot）只冒烟，见 test_tui_tech_mode.py。
"""

from __future__ import annotations

import pandas as pd

from src.tui.widgets.kline_chart import visible_window

# ──────────────────────────────────────────────────────────────────────
# visible_window：计算 cursor 周围的可见窗口索引范围
# 规则（ADR-0001 决策3）：
#   - 窗口大小默认 200 根
#   - cursor 在当前窗口内 → 返回当前窗口（不滚动）
#   - cursor 移出窗口 → 窗口跟随滚动到 cursor 可见
#   - cursor 靠近末尾 → 窗口对齐末尾（不越过数据尾部）
#   - cursor 靠近开头 → 窗口对齐开头（不越过数据头部）
# ──────────────────────────────────────────────────────────────────────


def _dates(n: int) -> list[pd.Timestamp]:
    return list(pd.date_range("2024-01-01", periods=n, freq="D"))


def test_window_short_data_returns_all() -> None:
    """数据少于窗口大小 → 窗口覆盖全部。"""
    dates = _dates(50)
    sl = visible_window(dates, cursor_idx=49, size=200)
    assert sl.start == 0
    assert sl.stop == 50


def test_cursor_at_end_window_is_last_size() -> None:
    """cursor 在末尾，数据 >= size → 窗口是最后 size 根。"""
    dates = _dates(300)
    sl = visible_window(dates, cursor_idx=299, size=200)
    assert sl.start == 100
    assert sl.stop == 300
    assert sl.stop - sl.start == 200


def test_cursor_inside_window_keeps_window() -> None:
    """cursor 在当前窗口内 → 窗口不动（停在最近 size 根）。

    初始窗口是最后 200 根 [100:300]，cursor=250 在内 → 窗口不变。
    """
    dates = _dates(300)
    sl = visible_window(dates, cursor_idx=250, size=200)
    assert sl.start == 100
    assert sl.stop == 300


def test_cursor_before_window_scrolls_left() -> None:
    """cursor 移到窗口左侧之外 → 窗口向左滚到 cursor 可见。

    初始窗口 [100:300]，cursor=50 在左外 → 滚动使 cursor 进窗口。
    滚动策略：cursor 作为窗口右端则窗口 [50-200:50]=[-150:50]，但负数 clamp 到 0；
    更合理：cursor 进窗口即停在它原本位置或居中。这里约定 cursor 至少可见，
    窗口右端 = max(cursor+1, ...)。本测试只断言 cursor 落进窗口、窗口不超界。
    """
    dates = _dates(300)
    sl = visible_window(dates, cursor_idx=50, size=200)
    assert sl.start <= 50 < sl.stop  # cursor 可见
    assert sl.start >= 0
    assert sl.stop <= 300
    assert sl.stop - sl.start <= 200


def test_cursor_at_start_window_clamps_to_head() -> None:
    """cursor 在数据开头 → 窗口对齐头部 [0:size]。"""
    dates = _dates(300)
    sl = visible_window(dates, cursor_idx=0, size=200)
    assert sl.start == 0
    assert sl.stop == 200
    assert 0 <= 200  # cursor 可见


def test_window_never_exceeds_data_bounds() -> None:
    """窗口无论 cursor 在哪都不越过 [0, len(dates)]。"""
    dates = _dates(250)
    for idx in range(250):
        sl = visible_window(dates, cursor_idx=idx, size=200)
        assert sl.start >= 0
        assert sl.stop <= 250
        assert sl.start <= idx < sl.stop  # cursor 始终可见


def test_empty_dates_returns_empty_slice() -> None:
    """空 dates → 返回空 slice，不崩。"""
    sl = visible_window([], cursor_idx=0, size=200)
    assert sl.start == 0
    assert sl.stop == 0


def test_size_larger_than_data_covers_all() -> None:
    """size > len → 窗口覆盖全部，cursor 必在内。"""
    dates = _dates(30)
    sl = visible_window(dates, cursor_idx=15, size=200)
    assert sl.start == 0
    assert sl.stop == 30
    assert sl.start <= 15 < sl.stop
