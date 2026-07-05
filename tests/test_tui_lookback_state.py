"""技术分析视图纯逻辑状态机测试 — 回看光标 / 副图槽位 / 主图叠加开关。

不依赖 Textual、不依赖真实数据。用合成 dates 列表驱动。
"""

import pandas as pd

from src.tui.state import LookbackCursor, OverlayToggles, SubplotSlots, TechView

# ---------- LookbackCursor ----------


def test_cursor_defaults_to_last_date() -> None:
    """构造后光标默认停在最后一根 K 线。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    assert cursor.current() == dates[-1]


def test_move_left_goes_back_one_bar() -> None:
    """move_left 后光标到前一根。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_left()
    assert cursor.current() == dates[-2]


def test_move_right_returns_to_end() -> None:
    """从倒数第二根 move_right 回到最后一根。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_left()
    cursor.move_right()
    assert cursor.current() == dates[-1]


def test_move_right_at_end_does_not_overflow() -> None:
    """在最后一根 move_right 不越界，仍停在 dates[-1]。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_right()
    assert cursor.current() == dates[-1]
    cursor.move_right(10)
    assert cursor.current() == dates[-1]


def test_move_left_at_start_does_not_underflow() -> None:
    """在第一根 move_left 不越界，仍停在 dates[0]。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_left(100)  # 一把拉到开头
    assert cursor.current() == dates[0]
    cursor.move_left()  # 已在开头再 left
    assert cursor.current() == dates[0]


def test_move_left_multiple_steps() -> None:
    """move_left(5) 一次往前 5 根。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_left(5)
    assert cursor.current() == dates[-6]  # 索引 14


def test_reset_to_end() -> None:
    """reset_to_end 从任意位置回到最后一根。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    cursor.move_left(5)
    assert cursor.current() != dates[-1]
    cursor.reset_to_end()
    assert cursor.current() == dates[-1]


def test_at_start_and_at_end() -> None:
    """at_start/at_end 在首尾返回正确布尔。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    cursor = LookbackCursor(dates=dates)
    assert cursor.at_end() is True
    assert cursor.at_start() is False
    cursor.move_left(100)  # 拉到开头
    assert cursor.at_start() is True
    assert cursor.at_end() is False


def test_empty_dates_does_not_crash() -> None:
    """空 dates 列表构造不崩，current 返回 None。"""
    cursor = LookbackCursor(dates=[])
    assert cursor.current() is None
    # 移动/重置/边界判定都不抛
    cursor.move_left()
    cursor.move_right()
    cursor.reset_to_end()
    assert cursor.at_start() is False
    assert cursor.at_end() is False


# ---------- SubplotSlots ----------


def test_subplot_slots_default() -> None:
    """默认 slot1=RSI, slot2=MACD。"""
    slots = SubplotSlots()
    assert slots.current() == ("RSI", "MACD")


def test_cycle_slot1_skips_slot2() -> None:
    """cycle_slot1 轮到下一个候选，跳过 slot2 占用的 MACD。"""
    slots = SubplotSlots()  # RSI, MACD
    slots.cycle_slot1()
    # RSI 的下一个候选是 MACD，但被 slot2 占用，跳过到 Stoch
    assert slots.current() == ("Stoch", "MACD")


def test_cycle_slot2_skips_slot1() -> None:
    """cycle_slot2 轮到下一个候选，跳过 slot1 占用的。"""
    slots = SubplotSlots()  # RSI, MACD
    slots.cycle_slot2()
    # MACD 的下一个是 Stoch，与 slot1 的 RSI 不冲突
    assert slots.current() == ("RSI", "Stoch")


def test_cycle_loops_back_and_never_collides() -> None:
    """连续 cycle 最终循环回起点；两槽位永不同值。"""
    slots = SubplotSlots()
    seen: set[tuple[str, str]] = set()
    # slot1 在 5 候选中轮，跳过 slot2，应能遍历回到 RSI
    for _ in range(4):  # 4 次后回到 RSI（共 5 候选，每跳 1 个被占）
        slots.cycle_slot1()
        assert slots.slot1 != slots.slot2  # 永不碰撞
        seen.add(slots.current())
    assert slots.slot1 == "RSI"  # 回到起点


def test_cycle_slot2_loops_back() -> None:
    """slot2 连续 cycle 也能循环回起点。"""
    slots = SubplotSlots()
    for _ in range(4):
        slots.cycle_slot2()
        assert slots.slot1 != slots.slot2
    assert slots.slot2 == "MACD"


# ---------- OverlayToggles ----------


def test_overlay_toggles_default() -> None:
    """默认 MA5/10/20/60 + 布林带开，MA120 + SuperTrend 关。"""
    ov = OverlayToggles()
    active = ov.active_overlays()
    for ma in ("MA5", "MA10", "MA20", "MA60"):
        assert ma in active
    assert "BB" in active
    assert "MA120" not in active
    assert "SuperTrend" not in active


def test_toggle_ma120_on_then_off() -> None:
    """toggle_ma120 开启后 active 含 MA120，再 toggle 关闭。"""
    ov = OverlayToggles()
    ov.toggle_ma120()
    assert "MA120" in ov.active_overlays()
    ov.toggle_ma120()
    assert "MA120" not in ov.active_overlays()


def test_toggle_bollinger_and_supertrend() -> None:
    """bollinger 和 supertrend 开关双向生效。"""
    ov = OverlayToggles()
    # 默认 BB 开，关掉
    ov.toggle_bollinger()
    assert "BB" not in ov.active_overlays()
    ov.toggle_bollinger()
    assert "BB" in ov.active_overlays()
    # 默认 SuperTrend 关，打开
    ov.toggle_supertrend()
    assert "SuperTrend" in ov.active_overlays()
    ov.toggle_supertrend()
    assert "SuperTrend" not in ov.active_overlays()


# ---------- TechView 聚合 ----------


def test_techview_resets_cursor_on_symbol_change() -> None:
    """切标的时光标重置到末尾（决策点1）。"""
    dates = list(pd.date_range("2024-01-01", periods=20))
    view = TechView()
    view.cursor = LookbackCursor(dates=dates)
    view.cursor.move_left(5)
    assert not view.cursor.at_end()
    # 模拟切标的：传入新 df 的日期索引
    new_dates = list(pd.date_range("2025-01-01", periods=10))
    view.on_symbol_changed(new_dates)
    assert view.cursor.at_end()
    assert view.cursor.current() == new_dates[-1]


def test_techview_default_components() -> None:
    """TechView 默认持有 cursor/slots/overlays 三组件。"""
    view = TechView()
    assert isinstance(view.slots, SubplotSlots)
    assert isinstance(view.overlays, OverlayToggles)
    assert view.slots.current() == ("RSI", "MACD")
    assert "BB" in view.overlays.active_overlays()
