"""宏观模式 TUI 冒烟测试。

Textual `async with app.run_test()` + pilot.pause 给 Worker 时间。
只测 widget 存在 + state 变化 + 不崩，不测 plotext 像素/颜色/线条视觉。
FRED 数据存在则用真实 CSV（每分类当前仅 1 行，期限光标移不动但不崩）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tui.app import KlineApp
from src.tui.widgets.macro_chart import MacroChart

RATES_CSV = Path("data/fred/rates/rates.csv")
VOL_CSV = Path("data/fred/volatility/volatility.csv")


def _skip_if_no_fred() -> None:
    if not RATES_CSV.exists():
        pytest.skip("无 FRED rates 数据")


async def _select_macro(app: KlineApp, pilot, category: str, series: str) -> None:
    """直接设选中态 + 切宏观 + 等数据加载完成（MacroChart 出现且 df 就绪）。"""
    screen = app.query_one("MainScreen")
    screen.state.switch_mode("macro")
    await screen.swap_sidebar()
    screen.state.select_macro(category, series)
    await screen.refresh_content()
    chart = None
    for _ in range(30):
        await pilot.pause()
        try:
            chart = app.query_one(MacroChart)
            if chart.df is not None:
                break
        except Exception:
            chart = None
    assert chart is not None, "MacroChart 未在限时内加载完成"


async def test_macro_mode_shows_chart_after_select() -> None:
    """选中 rates/DGS10 后，内容区出现 MacroChart（query_one 不抛错）。"""
    _skip_if_no_fred()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "rates", "DGS10")
        chart = app.query_one(MacroChart)
        assert chart.df is not None
        assert "DGS10" in chart.df.columns


async def test_space_toggle_adds_series_to_overlay() -> None:
    """选中 DGS10 后按空格，overlaid_series 含 DGS10。"""
    _skip_if_no_fred()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "rates", "DGS10")
        screen = app.query_one("MainScreen")
        assert "DGS10" not in screen.macro_view.overlaid_series
        await pilot.press("space")
        await pilot.pause()
        assert "DGS10" in screen.macro_view.overlaid_series
        # 再按一次移除
        await pilot.press("space")
        await pilot.pause()
        assert "DGS10" not in screen.macro_view.overlaid_series


async def test_term_structure_visible_in_rates() -> None:
    """rates 分类下期限结构子图可见（display=True，不抛错）。"""
    _skip_if_no_fred()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "rates", "DGS10")
        chart = app.query_one(MacroChart)
        term = chart.query_one("#macro-term")
        assert term.display is True


async def test_term_structure_hidden_in_non_term_category() -> None:
    """非期限分类（volatility）下期限结构子图隐藏。"""
    if not VOL_CSV.exists():
        pytest.skip("无 FRED volatility 数据")
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "volatility", "VIX")
        chart = app.query_one(MacroChart)
        term = chart.query_one("#macro-term")
        assert term.display is False


async def test_arrow_keys_move_term_cursor_in_rates() -> None:
    """rates 分类下 ←→ 移 term_cursor 不崩（数据仅 1 行则 clamp 不动）。"""
    _skip_if_no_fred()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "rates", "DGS10")
        screen = app.query_one("MainScreen")
        await pilot.press("left")
        await pilot.press("right")
        await pilot.pause()
        # 不崩即可；单行数据下 current 仍 clamp 在唯一日期
        assert screen.macro_view.term_cursor is not None


async def test_switching_category_resets_overlay() -> None:
    """先在 rates 叠加 DGS10，再切到 volatility 后叠加集合被清空。"""
    if not VOL_CSV.exists() or not RATES_CSV.exists():
        pytest.skip("无 FRED 数据")
    app = KlineApp()
    async with app.run_test() as pilot:
        await _select_macro(app, pilot, "rates", "DGS10")
        screen = app.query_one("MainScreen")
        await pilot.press("space")
        await pilot.pause()
        assert "DGS10" in screen.macro_view.overlaid_series
        # 切到 volatility（选 VIX 叶节点）
        screen.state.select_macro("volatility", "VIX")
        await screen.refresh_content()
        for _ in range(20):
            await pilot.pause()
        assert screen.macro_view.overlaid_series == set()
