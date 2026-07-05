"""技术分析模式 TUI 冒烟测试。

Textual `async with app.run_test()` + pilot.pause 给 Worker 时间。
只测 widget 存在 + state 变化 + 不崩，不测 plotext 像素/颜色/vline 视觉位置。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tui.app import KlineApp
from src.tui.widgets.diag_sidebar import DiagSidebar
from src.tui.widgets.kline_chart import KlineChart

AAPL_CSV = Path("data/stocks/AAPL.csv")


def _skip_if_no_data() -> None:
    if not AAPL_CSV.exists():
        pytest.skip("无 AAPL 数据")


async def _load_aapl(app: KlineApp, pilot) -> None:
    """选中 AAPL 并等 Worker 加载完成（KlineChart 出现且 df 就绪）。"""
    screen = app.query_one("MainScreen")
    screen.state.select_tech("AAPL")
    await screen.refresh_content()
    # Worker 在后台线程加载，轮询等待完成（最多 ~30 次 pause）
    chart = None
    for _ in range(30):
        await pilot.pause()
        try:
            chart = app.query_one(KlineChart)
            if chart.df is not None:
                break
        except Exception:
            chart = None
    assert chart is not None, "KlineChart 未在限时内加载完成"


async def test_tech_mode_shows_kline_chart_after_select() -> None:
    """选中 AAPL 后，内容区出现 KlineChart widget（query_one 不抛错）。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        chart = app.query_one(KlineChart)
        assert chart.df is not None
        assert len(chart.df) > 0


async def test_tech_mode_diag_sidebar_shows_score() -> None:
    """选中后 DiagSidebar 显示评分文本（含'评分'字样）。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        sidebar = app.query_one(DiagSidebar)
        # render_diagnosis 是 update()，文本在 renderable 里
        await pilot.pause()
        # 取 Static 的渲染文本
        text = str(sidebar.render())
        assert "评分" in text


async def test_left_arrow_moves_cursor_backward() -> None:
    """按 ← 后 tech_view.cursor.current() 往前移一根。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        screen = app.query_one("MainScreen")
        before = screen.tech_view.cursor.current()
        await pilot.press("left")
        await pilot.pause()
        after = screen.tech_view.cursor.current()
        assert after < before  # 往前移（日期更早）


async def test_left_arrow_does_not_crash_chart() -> None:
    """按 ← 后 KlineChart 仍存在（重画没崩）。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        await pilot.press("left")
        await pilot.press("left")
        await pilot.press("left")
        await pilot.pause()
        chart = app.query_one(KlineChart)
        assert chart.df is not None  # 重画后 df 仍在


async def test_right_arrow_moves_cursor_forward() -> None:
    """按 → 后 cursor 往后移一根；在末尾时不越界。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        screen = app.query_one("MainScreen")
        # 先 left 几次再 right
        await pilot.press("left")
        await pilot.press("left")
        await pilot.pause()
        mid = screen.tech_view.cursor.current()
        await pilot.press("right")
        await pilot.pause()
        after = screen.tech_view.cursor.current()
        assert after > mid


async def test_left_arrow_refreshes_diag_sidebar_debounced() -> None:
    """按 ← 后（防抖到期）侧栏诊断更新到 cursor 日期。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        screen = app.query_one("MainScreen")
        # 移到较远的历史日期，诊断的 last_date 应跟着变
        await pilot.press("left")
        await pilot.press("left")
        await pilot.press("left")
        await pilot.press("left")
        await pilot.press("left")
        # 防抖 50ms + 重画，多 pause 几次确保到期
        for _ in range(10):
            await pilot.pause()
        cursor_date = screen.tech_view.cursor.current()
        sidebar = app.query_one(DiagSidebar)
        text = str(sidebar.render())
        # 诊断文本里的日期应是 cursor 日期（YYYY-MM-DD），不是末尾
        assert str(cursor_date.date()) in text


async def test_cycle_subplot_key_does_not_crash() -> None:
    """按 1/2 切副图不崩，KlineChart 仍在。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        await pilot.press("1")
        await pilot.press("2")
        await pilot.pause()
        chart = app.query_one(KlineChart)
        assert chart.df is not None


async def test_toggle_overlay_key_does_not_crash() -> None:
    """按 b/m/s 切叠加层不崩，KlineChart 仍在。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        await pilot.press("b")
        await pilot.press("m")
        await pilot.press("s")
        await pilot.pause()
        chart = app.query_one(KlineChart)
        assert chart.df is not None


async def test_keys_in_macro_mode_do_not_affect_tech_view() -> None:
    """MACRO 模式下 ←/→/1/2 不应影响 tech_view（键盘回看只在 TECH 生效）。"""
    _skip_if_no_data()
    app = KlineApp()
    async with app.run_test() as pilot:
        await _load_aapl(app, pilot)
        screen = app.query_one("MainScreen")
        # 切到宏观
        await pilot.press("tab")
        assert screen.state.mode == "macro"
        tech_cursor_before = screen.tech_view.cursor.current()
        await pilot.press("left")
        await pilot.press("1")
        await pilot.pause()
        # tech_view 没变
        assert screen.tech_view.cursor.current() == tech_cursor_before
