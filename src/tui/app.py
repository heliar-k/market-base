"""KlineApp — K线分析 TUI 主入口。

双模式（技术分析 / 宏观）+ 三栏布局 + Tab 切模式。
骨架阶段：只跑通"选标的→加载缓存→显示标的名和最新价"最小链路。
画 K 线 / 回看交互 / 宏观图表渲染留给后续步骤。
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ListView, Tree

from src.tui.screens import MainScreen
from src.tui.state import Mode


class KlineApp(App):
    """K线分析 TUI 主应用。"""

    CSS = """
    Screen { background: $surface; }
    """

    # priority=True 让 Tab 优先于默认的焦点遍历，由 App 统一处理为切模式
    BINDINGS = [
        Binding("tab", "cycle_mode", "切模式", priority=True),
        Binding("shift+tab", "cycle_mode", "切模式", priority=True),
        ("q", "quit", "退出"),
    ]

    def compose(self) -> ComposeResult:
        yield MainScreen()

    def on_mount(self) -> None:
        self.title = "K线分析"

    # ── 键盘：Tab / shift+Tab 切模式 ────────────────────────────────────
    def action_cycle_mode(self) -> None:
        screen = self.query_one(MainScreen)
        next_mode = Mode.MACRO if screen.state.mode == Mode.TECH else Mode.TECH
        screen.state.switch_mode(next_mode)
        self.call_after_refresh(self._swap)

    async def _swap(self) -> None:
        await self.query_one(MainScreen).swap_sidebar()

    # ── 键盘：ListView / Tree 原生 ↑↓ 导航；Enter 选中 ──────────────────
    @on(ListView.Selected)
    def _on_tech_selected(self, event: ListView.Selected) -> None:
        # id 形如 "tech-AAPL"
        item_id = event.item.id or ""
        symbol = item_id.split("-", 1)[1] if "-" in item_id else ""
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
