"""TUI 主 Screen：三栏布局 + 模式切换 swap。

布局（CSS）：
  ┌──────────┬───────────────────────┐
  │ 侧栏     │  内容区                │
  │ (导航)   │  (选中项信息)          │
  │          │                        │
  ├──────────┴───────────────────────┤
  │ 状态栏 (模式 + 提示)              │
  └──────────────────────────────────┘
侧栏按 mode 在 ListView（技术分析）/ Tree（宏观）间 swap。
本步只做骨架：内容区显示选中项基本信息，不画图、不交互回看。
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import ListItem, ListView, Static, Tree

from src.config import IBKR_SYMBOLS
from src.tui.state import Mode, TuiState


class StatusBar(Static):
    """底部状态栏：当前模式 + 操作提示。"""

    def render_text(self, mode: str) -> None:
        if mode == Mode.MACRO:
            self.update("MACRO | 选分类→系列(待实现绘图) | Tab切模式 | q退出")
        else:
            self.update("TECH | ←→回看(待实现) | Tab切模式 | q退出")


class TechListView(ListView):
    """技术分析模式侧栏：平铺所有 IBKR 标的。"""

    def __init__(self) -> None:
        super().__init__(
            *[ListItem(Static(s["name"]), id=f"tech-{s['name']}") for s in IBKR_SYMBOLS]
        )


class MacroTree(Tree):
    """宏观模式侧栏：两级树 分类→系列。"""

    def __init__(self) -> None:
        # ponytail: 延迟 import FRED_SERIES 避免顶层循环；实际无循环，但保持局部化
        from src.config import FRED_SERIES

        super().__init__("FRED", id="macro-tree")
        for category, series_map in FRED_SERIES.items():
            node = self.root.add(category, allow_expand=True)
            for metric in series_map:
                node.add_leaf(metric)


class MainScreen(Container):
    """主屏：三栏 + 模式切换。

    状态逻辑委托给 TuiState（纯逻辑），widget 只做渲染 + 把事件翻译成状态调用。
    """

    CSS = """
    MainScreen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 24; border-right: solid $primary; overflow: auto; }
    #content { padding: 0 1; }
    #statusbar { height: 1; background: $boost; dock: bottom; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.state = TuiState()
        self.content_text = ""  # 最近渲染到内容区的文本（可观测，供测试/状态用）

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Container(TechListView(), id="sidebar")
            yield Container(Static("选择标的以加载", id="content-text"), id="content")
        yield StatusBar("TECH | ←→回看(待实现) | Tab切模式 | q退出", id="statusbar")

    # ── 模式切换：swap 侧栏 widget ──────────────────────────────────────
    async def swap_sidebar(self) -> None:
        """按当前 state.mode 把侧栏换成 ListView / Tree。"""
        sidebar = self.query_one("#sidebar", Container)
        # 移除现有导航 widget
        for child in list(sidebar.children):
            await child.remove()
        if self.state.mode == Mode.TECH:
            await sidebar.mount(TechListView())
        else:
            await sidebar.mount(MacroTree())
        self.query_one("#statusbar", StatusBar).render_text(self.state.mode)
        await self.refresh_content()

    # ── 内容区刷新：根据选中态显示基本信息 ───────────────────────────────
    async def refresh_content(self) -> None:
        """把当前选中项的基本信息渲染到内容区。"""
        text = self._render_selection()
        self.content_text = text
        self.query_one("#content-text", Static).update(text)

    def _render_selection(self) -> str:
        sel = self.state.current_selection()
        if sel is None:
            if self.state.mode == Mode.MACRO:
                return "宏观模式：选择分类→系列"
            return "技术分析模式：选择标的"
        if self.state.mode == Mode.MACRO:
            category, series = sel
            return f"{category} / {series}"
        # 技术分析：尝试加载缓存显示最新价 + 日期
        return self._render_tech(sel)

    def _render_tech(self, symbol: str) -> str:
        """技术分析选中标的：调 load_or_compute 显示最新价 + 日期。CSV 缺失则提示。"""
        from src.cache import load_or_compute

        csv_path = self._csv_path_for(symbol)
        if csv_path is None or not csv_path.exists():
            return f"{symbol}：无数据，请先 ./bin/fetch_ibkr --symbols {symbol}"
        df = load_or_compute(symbol, csv_path)
        last_close = df["close"].iloc[-1]
        last_date = df.index[-1]
        return f"{symbol} ${last_close:.2f} {last_date.strftime('%Y-%m-%d')}"

    @staticmethod
    def _csv_path_for(symbol: str) -> Path | None:
        """根据 IBKR_SYMBOLS 的 type 字段返回对应 CSV 路径。"""
        for entry in IBKR_SYMBOLS:
            if entry["name"] == symbol:
                kind = entry["type"]
                sub = "stocks" if kind == "stock" else "indices"
                return Path("data") / sub / f"{symbol}.csv"
        return None
