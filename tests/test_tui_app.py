"""TUI App Textual 冒烟测试。

只验证关键行为（启动 / 模式切换 swap / 选中态保留），不测 CSS、像素、渲染细节。
asyncio_mode=auto（见 pyproject），无需逐个加 @pytest.mark.asyncio。
"""

from textual.widgets import ListView

from src.tui.app import KlineApp
from src.tui.state import Mode


async def test_app_starts() -> None:
    """App 能在 headless 模式启动，不抛错。"""
    app = KlineApp()
    async with app.run_test():
        pass  # 启动即成功


async def test_default_mode_is_tech_with_listview() -> None:
    """启动后默认在 TECH 模式，侧栏是 ListView。"""
    app = KlineApp()
    async with app.run_test():
        screen = app.query_one("MainScreen")
        assert screen.state.mode == Mode.TECH
        assert app.query_one("#sidebar").query(ListView) is not None
        # 侧栏里有 ListView，且不含 Tree
        assert len(app.screen.query("ListView")) == 1
        assert len(app.screen.query("Tree")) == 0


async def test_tab_switches_to_macro_with_tree() -> None:
    """按 Tab 切到 MACRO 模式，侧栏 swap 成 Tree。"""
    app = KlineApp()
    async with app.run_test() as pilot:
        await pilot.press("tab")
        screen = app.query_one("MainScreen")
        assert screen.state.mode == Mode.MACRO
        # 侧栏现在是 Tree，不再有 ListView
        assert len(app.screen.query("Tree")) == 1
        assert len(app.screen.query("ListView")) == 0


async def test_tab_back_to_tech_restores_listview_and_selection() -> None:
    """再按 Tab 切回 TECH：侧栏变回 ListView，且之前选中的标的状态保留。"""
    app = KlineApp()
    async with app.run_test() as pilot:
        screen = app.query_one("MainScreen")
        # 在 TECH 选 AAPL
        screen.state.select_tech("AAPL")
        assert screen.state.tech_selected == "AAPL"
        # 切到宏观再切回
        await pilot.press("tab")
        assert screen.state.mode == Mode.MACRO
        await pilot.press("tab")
        assert screen.state.mode == Mode.TECH
        # 侧栏变回 ListView
        assert len(app.screen.query("ListView")) == 1
        assert len(app.screen.query("Tree")) == 0
        # 选中态保留
        assert screen.state.tech_selected == "AAPL"


async def test_select_tech_loads_price_into_content() -> None:
    """TECH 模式选中标的后，内容区显示最新价 + 日期（走 load_or_compute）。"""
    from pathlib import Path

    if not Path("data/stocks/AAPL.csv").exists():
        return  # 无数据时跳过，不强制
    app = KlineApp()
    async with app.run_test():
        screen = app.query_one("MainScreen")
        screen.state.select_tech("AAPL")
        await screen.refresh_content()
        rendered = screen.content_text
        assert rendered.startswith("AAPL $")
        assert "20" in rendered  # 年份片段
