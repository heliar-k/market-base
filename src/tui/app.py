"""KlineApp — ticker-toolkit TUI 主入口。

双模式（技术分析 / 宏观）+ 三栏布局 + Tab 切模式。
技术分析模式：K 线图三层（主图 candlestick+叠加 + 2 副图）+ 键盘 ←→ 回看 +
侧栏诊断（Worker 化加载 + 50ms 防抖刷新）。双模式均已实现。
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ListView, Tree

from src.tui.screens import MainScreen
from src.tui.state import Mode


class KlineApp(App):
    """ticker-toolkit TUI 主应用。"""

    CSS = """
    Screen { background: #0d1117; }
    * { scrollbar-color: #30363d; scrollbar-color-active: #484f58; }
    ListView { background: #161b22; }
    ListView > ListItem { padding: 0 1; color: #8b949e; }
    ListView > ListItem.--highlight { background: #1f6feb; color: #ffffff; }
    Tree { background: #161b22; }
    Tree > .tree--label { color: #8b949e; }
    Tree > .tree--guides { color: #30363d; }
    Tree > .tree--guides-hover { color: #58a6ff; }
    """

    # priority=True 让 Tab 优先于默认的焦点遍历，由 App 统一处理为切模式
    # 技术分析模式：←/→ 回看，1/2 切副图，b/m/s 切叠加层；只在 TECH 模式生效
    BINDINGS = [
        Binding("tab", "cycle_mode", "切模式", priority=True),
        Binding("shift+tab", "cycle_mode", "切模式", priority=True),
        Binding("left", "lookback_left", "回看←", priority=True),
        Binding("right", "lookback_right", "回看→", priority=True),
        Binding("1", "cycle_subplot1", "副图1", priority=True),
        Binding("2", "cycle_subplot2", "副图2", priority=True),
        Binding("b", "toggle_bb", "布林带", priority=True),
        Binding("m", "toggle_ma120", "MA120", priority=True),
        Binding("s", "toggle_supertrend", "SuperTrend", priority=True),
        Binding("space", "toggle_macro_series", "叠加系列", priority=True),
        ("q", "quit", "退出"),
    ]

    def compose(self) -> ComposeResult:
        yield MainScreen()

    def on_mount(self) -> None:
        self.title = "ticker-toolkit"

    # ── 键盘：Tab / shift+Tab 切模式 ────────────────────────────────────
    def action_cycle_mode(self) -> None:
        screen = self.query_one(MainScreen)
        next_mode = Mode.MACRO if screen.state.mode == Mode.TECH else Mode.TECH
        screen.state.switch_mode(next_mode)
        self.call_after_refresh(self._swap)

    async def _swap(self) -> None:
        await self.query_one(MainScreen).swap_sidebar()

    # ── 键盘：技术分析回看 / 副图 / 叠加层（仅 TECH 模式生效）──────────────
    def _screen(self) -> MainScreen:
        return self.query_one(MainScreen)

    def _is_tech(self) -> bool:
        return self._screen().state.mode == Mode.TECH

    def action_lookback_left(self) -> None:
        if self._is_tech():
            self._screen().move_cursor("left")
        else:
            self._screen().move_term_cursor("left")

    def action_lookback_right(self) -> None:
        if self._is_tech():
            self._screen().move_cursor("right")
        else:
            self._screen().move_term_cursor("right")

    def action_cycle_subplot1(self) -> None:
        if self._is_tech():
            self._screen().cycle_subplot(1)

    def action_cycle_subplot2(self) -> None:
        if self._is_tech():
            self._screen().cycle_subplot(2)

    def action_toggle_bb(self) -> None:
        if self._is_tech():
            self._screen().toggle_overlay("b")

    def action_toggle_ma120(self) -> None:
        if self._is_tech():
            self._screen().toggle_overlay("m")

    def action_toggle_supertrend(self) -> None:
        if self._is_tech():
            self._screen().toggle_overlay("s")

    def action_toggle_macro_series(self) -> None:
        """空格 toggle 当前选中宏观系列到叠加集合（仅 MACRO 模式）。"""
        if not self._is_tech():
            self._screen().toggle_macro_series()

    # ── 键盘：ListView / Tree 原生 ↑↓ 导航；Enter 选中 ──────────────────
    @on(ListView.Selected)
    def _on_tech_selected(self, event: ListView.Selected) -> None:
        # 标的名取自 ListItem 内 Static 的文本，避免用品种名当 DOM id
        # （BRK.B 含点违反 Textual 标识符规则）
        symbol = str(event.item.children[0].renderable) if event.item.children else ""
        if not symbol:
            return
        screen = self.query_one(MainScreen)
        screen.state.select_tech(symbol)
        self.call_after_refresh(screen.refresh_content)

    @on(Tree.NodeSelected)
    def _on_macro_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        # 叶节点（系列）才处理：父节点是分类
        if node.children:
            return  # 点的是分类节点，展开/折叠由 Tree 原生处理
        series = str(node.label)
        parent = node.parent
        category = str(parent.label) if parent is not None else ""
        screen = self.query_one(MainScreen)
        screen.state.select_macro(category, series)
        self.call_after_refresh(screen.refresh_content)


if __name__ == "__main__":
    KlineApp().run()
