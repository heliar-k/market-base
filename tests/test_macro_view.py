"""MacroView 状态机 + TERM_SERIES 纯逻辑测试 — 不依赖 Textual。"""

from __future__ import annotations

import pandas as pd

from src.config import TERM_SERIES
from src.tui.state import MacroView

# ──────────────────────────────────────────────────────────────────────
# TERM_SERIES：哪些分类有期限结构 + 期限顺序
# ──────────────────────────────────────────────────────────────────────


def test_term_series_has_rates_and_tips_only() -> None:
    """TERM_SERIES 只含 rates / tips 两类（国债收益率曲线才有期限结构）。"""
    assert set(TERM_SERIES.keys()) == {"rates", "tips"}


def test_term_series_rates_has_11_in_order() -> None:
    """rates 期限系列 11 个，按 1mo→30y 升序。"""
    expected = [
        "DGS1MO",
        "DGS3MO",
        "DGS6MO",
        "DGS1",
        "DGS2",
        "DGS3",
        "DGS5",
        "DGS7",
        "DGS10",
        "DGS20",
        "DGS30",
    ]
    assert TERM_SERIES["rates"] == expected
    assert len(TERM_SERIES["rates"]) == 11


def test_term_series_tips_has_5_in_order() -> None:
    """tips 期限系列 5 个，按 5y→30y 升序。"""
    assert TERM_SERIES["tips"] == ["DFII5", "DFII7", "DFII10", "DFII20", "DFII30"]


# ──────────────────────────────────────────────────────────────────────
# MacroView：默认空叠加集合
# ──────────────────────────────────────────────────────────────────────


def test_macro_view_default_empty_overlay() -> None:
    """新建 MacroView 默认无叠加系列、无分类、无 term_cursor。"""
    mv = MacroView()
    assert mv.category is None
    assert mv.overlaid_series == set()
    assert mv.term_cursor is None


# ──────────────────────────────────────────────────────────────────────
# toggle_series：加 / 减
# ──────────────────────────────────────────────────────────────────────


def test_toggle_series_adds_to_overlay() -> None:
    """toggle 一次后系列进叠加集合。"""
    mv = MacroView()
    mv.toggle_series("DGS10")
    assert "DGS10" in mv.overlaid_series


def test_toggle_series_removes_when_already_in() -> None:
    """已叠加的系列再 toggle 一次则移除。"""
    mv = MacroView()
    mv.toggle_series("DGS10")
    assert "DGS10" in mv.overlaid_series
    mv.toggle_series("DGS10")
    assert "DGS10" not in mv.overlaid_series


def test_toggle_multiple_series() -> None:
    """toggle 多个系列后叠加集合正确。"""
    mv = MacroView()
    mv.toggle_series("DGS10")
    mv.toggle_series("DGS2")
    mv.toggle_series("SOFR")
    assert mv.overlaid_series == {"DGS10", "DGS2", "SOFR"}
    # 移除一个
    mv.toggle_series("DGS2")
    assert mv.overlaid_series == {"DGS10", "SOFR"}


# ──────────────────────────────────────────────────────────────────────
# on_category_changed：清空叠加 + 重建 term_cursor
# ──────────────────────────────────────────────────────────────────────


def _dates(n: int) -> list[pd.Timestamp]:
    return list(pd.date_range("2024-01-01", periods=n, freq="D"))


def test_on_category_changed_clears_overlay() -> None:
    """切分类时清空叠加集合。"""
    mv = MacroView()
    mv.toggle_series("DGS10")
    mv.toggle_series("SOFR")
    assert mv.overlaid_series != set()
    mv.on_category_changed("rates", _dates(5))
    assert mv.overlaid_series == set()


def test_on_category_changed_rates_builds_term_cursor() -> None:
    """切到 rates 时重建 term_cursor（指向最新日期）。"""
    mv = MacroView()
    dates = _dates(5)
    mv.on_category_changed("rates", dates)
    assert mv.term_cursor is not None
    assert mv.term_cursor.current() == dates[-1]


def test_on_category_changed_tips_builds_term_cursor() -> None:
    """切到 tips 时也建 term_cursor。"""
    mv = MacroView()
    dates = _dates(5)
    mv.on_category_changed("tips", dates)
    assert mv.term_cursor is not None


def test_on_category_changed_non_term_category_no_cursor() -> None:
    """非期限分类（如 volatility）不建 term_cursor。"""
    mv = MacroView()
    mv.on_category_changed("volatility", _dates(5))
    assert mv.term_cursor is None


def test_on_category_changed_sets_category() -> None:
    """切分类后 category 字段更新。"""
    mv = MacroView()
    mv.on_category_changed("rates", _dates(3))
    assert mv.category == "rates"


def test_on_category_changed_empty_dates_no_cursor() -> None:
    """切到 rates 但无数据时不崩，term_cursor 为 None 或指向 None。"""
    mv = MacroView()
    mv.on_category_changed("rates", [])
    # 空数据：cursor 重建但不指向任何日期（current() 返回 None）
    if mv.term_cursor is not None:
        assert mv.term_cursor.current() is None
