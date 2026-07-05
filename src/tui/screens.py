"""TUI 主 Screen：三栏布局 + 模式切换 swap。

布局（CSS）：
  ┌──────────┬────────────────────────┬──────────────┐
  │ 侧栏     │  内容区 (K线图)         │ 诊断侧栏     │
  │ (导航)   │  KlineChart             │ DiagSidebar  │
  │          │                         │              │
  ├──────────┴─────────────────────────┴──────────────┤
  │ 状态栏 (模式 + 提示)                               │
  └───────────────────────────────────────────────────┘
侧栏按 mode 在 ListView（技术分析）/ Tree（宏观）间 swap。
技术分析模式：选中标的 → Worker 化 load_or_compute → 画 K 线 + 初始诊断；
  ←→ 移 cursor（vline 跟手重画）+ 侧栏 analyze 防抖 50ms 刷新。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import ListItem, ListView, Static, Tree

from src.analyze import analyze
from src.cache import load_or_compute
from src.config import IBKR_SYMBOLS
from src.tui.state import Mode, TechView, TuiState
from src.tui.widgets.diag_sidebar import DiagSidebar
from src.tui.widgets.kline_chart import KlineChart

# 回看侧栏 analyze 防抖延迟（决策点5）：连续按 ← 时只在停顿后刷新诊断。
_DEBOUNCE_SECONDS = 0.05


class StatusBar(Static):
    """底部状态栏：当前模式 + 操作提示。"""

    def render_text(self, mode: str) -> None:
        if mode == Mode.MACRO:
            self.update("MACRO | 选分类→系列(待实现绘图) | Tab切模式 | q退出")
        else:
            self.update("TECH | ←→回看 | 1/2切副图 | b/m/s 切叠加 | Tab切模式 | q退出")


class TechListView(ListView):
    """技术分析模式侧栏：平铺所有 IBKR 标的。"""

    def __init__(self) -> None:
        super().__init__(
            *[ListItem(Static(s["name"]), id=f"tech-{s['name']}") for s in IBKR_SYMBOLS]
        )


class MacroTree(Tree):
    """宏观模式侧栏：两级树 分类→系列。"""

    def __init__(self) -> None:
        from src.config import FRED_SERIES

        super().__init__("FRED", id="macro-tree")
        for category, series_map in FRED_SERIES.items():
            node = self.root.add(category, allow_expand=True)
            for metric in series_map:
                node.add_leaf(metric)


class MainScreen(Container):
    """主屏：三栏 + 模式切换。

    状态逻辑委托给 TuiState（模式/选中态）+ TechView（技术分析视图状态）。
    widget 只做渲染 + 把事件翻译成状态调用。
    """

    CSS = """
    MainScreen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 24; border-right: solid $primary; overflow: auto; }
    #content { padding: 0 1; }
    #diag { width: 36; border-left: solid $primary; overflow: auto; padding: 0 1; }
    #statusbar { height: 1; background: $boost; dock: bottom; }
    /* K线图三层：主图占大头，两副图等高 */
    KlineChart { height: 1fr; }
    KlineChart > PlotextPlot { width: 1fr; }
    KlineChart > #kline-main { height: 3fr; }
    KlineChart > #kline-sub1 { height: 1fr; }
    KlineChart > #kline-sub2 { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.state = TuiState()
        self.tech_view = TechView()
        self.content_text = ""  # 宏观模式/无数据时的可观测文本
        self._current_df: pd.DataFrame | None = None  # 当前加载的 df（带指标）
        self._current_symbol: str | None = None
        self._debounce_timer = None  # 侧栏防抖定时器

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Container(TechListView(), id="sidebar")
            yield Container(Static("选择标的以加载", id="content-text"), id="content")
            yield Container(DiagSidebar("等待诊断", id="diag-text"), id="diag")
        yield StatusBar("TECH | ←→回看 | Tab切模式 | q退出", id="statusbar")

    # ── 模式切换：swap 侧栏 widget ──────────────────────────────────────
    async def swap_sidebar(self) -> None:
        """按当前 state.mode 把侧栏换成 ListView / Tree。"""
        sidebar = self.query_one("#sidebar", Container)
        for child in list(sidebar.children):
            await child.remove()
        if self.state.mode == Mode.TECH:
            await sidebar.mount(TechListView())
        else:
            await sidebar.mount(MacroTree())
        self.query_one("#statusbar", StatusBar).render_text(self.state.mode)
        await self.refresh_content()

    # ── 内容区刷新：根据选中态显示基本信息 / K 线图 ─────────────────────
    async def refresh_content(self) -> None:
        """把当前选中项渲染到内容区。技术分析走 Worker 化加载。"""
        sel = self.state.current_selection()
        placeholder = self.query_one("#content-text", Static)
        if sel is None:
            self.content_text = self._empty_hint()
            placeholder.display = True
            placeholder.update(self.content_text)
            self._set_chart_visible(False)
            return
        if self.state.mode == Mode.MACRO:
            category, series = sel
            self.content_text = f"{category} / {series}"
            placeholder.display = True
            placeholder.update(self.content_text)
            self._set_chart_visible(False)
            return
        # 技术分析：Worker 化加载
        self._load_tech_worker(sel)

    def _set_chart_visible(self, visible: bool) -> None:
        """切换 KlineChart 的显示状态（宏观/无数据时隐藏）。"""
        content = self.query_one("#content", Container)
        chart = content.query_one(KlineChart) if content.query(KlineChart) else None
        if chart is not None:
            chart.display = visible

    def _empty_hint(self) -> str:
        if self.state.mode == Mode.MACRO:
            return "宏观模式：选择分类→系列"
        return "技术分析模式：选择标的"

    # ── Worker 化加载：load_or_compute 放后台线程，不阻塞 UI ─────────────
    @work(thread=True, exclusive=True, name="tech-load")
    def _load_tech_worker(self, symbol: str) -> None:
        """后台加载 df；UI 先显示'加载中'，完成后画图 + 初始诊断。"""
        csv_path = self._csv_path_for(symbol)
        if csv_path is None or not csv_path.exists():
            self.app.call_from_thread(self._show_no_data, symbol)
            return
        # UI 提示加载中
        self.app.call_from_thread(
            self.query_one("#content-text", Static).update, f"{symbol} 加载中..."
        )
        df = load_or_compute(symbol, csv_path)
        self.app.call_from_thread(self._on_tech_loaded, symbol, df)

    def _show_no_data(self, symbol: str) -> None:
        self._current_df = None
        self._current_symbol = None
        msg = f"{symbol}：无数据，请先 ./bin/fetch_ibkr --symbols {symbol}"
        self.content_text = msg
        placeholder = self.query_one("#content-text", Static)
        placeholder.display = True
        placeholder.update(msg)

    def _on_tech_loaded(self, symbol: str, df: pd.DataFrame) -> None:
        """df 加载完成：reset cursor + 画图 + 初始诊断。"""
        self._current_df = df
        self._current_symbol = symbol
        dates = list(df.index)
        self.tech_view.on_symbol_changed(dates)
        # 把内容区从 Static 换成 KlineChart（如已存在则 update_data）
        content = self.query_one("#content", Container)
        chart = content.query_one(KlineChart) if content.query(KlineChart) else None
        if chart is None:
            chart = KlineChart(self.tech_view)
            # 隐藏占位 Static（保留在 DOM 以便切回宏观/无数据时复用）
            placeholder = self.query_one("#content-text", Static)
            placeholder.display = False
            content.mount(chart)
        chart.display = True
        chart.update_data(df)
        # 初始诊断（cursor 在末尾）
        result = analyze(df, symbol)
        self.query_one(DiagSidebar).render_diagnosis(result)

    # ── 回看：←/→ 移 cursor ─────────────────────────────────────────────
    def move_cursor(self, direction: str) -> None:
        """←/→ 移 cursor：vline 跟手重画 + 侧栏 analyze 防抖 50ms 刷新。"""
        if self._current_df is None or self._current_symbol is None:
            return
        content = self.query_one("#content", Container)
        chart = content.query_one(KlineChart) if content.query(KlineChart) else None
        if chart is None:
            return
        chart.move_cursor(direction)
        # 侧栏防抖：取消上一次未触发的，重新计时
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
        self._debounce_timer = self.set_timer(
            _DEBOUNCE_SECONDS, self._refresh_diag_debounced
        )

    def _refresh_diag_debounced(self) -> None:
        """防抖到期：用 cursor 日期截断 df 调 analyze 刷新侧栏。"""
        if self._current_df is None or self._current_symbol is None:
            return
        cur = self.tech_view.cursor.current()
        result = analyze(self._current_df, self._current_symbol, as_of=cur)
        self.query_one(DiagSidebar).render_diagnosis(result)

    # ── 副图槽位轮换 ────────────────────────────────────────────────────
    def cycle_subplot(self, slot: int) -> None:
        """slot=1/2 轮换对应副图指标，重画。"""
        if slot == 1:
            self.tech_view.slots.cycle_slot1()
        elif slot == 2:
            self.tech_view.slots.cycle_slot2()
        else:
            return
        self._redraw_chart()

    # ── 叠加层开关 ──────────────────────────────────────────────────────
    def toggle_overlay(self, which: str) -> None:
        """which ∈ {b, m, s} → 切 布林带/MA120/SuperTrend，重画。"""
        if which == "b":
            self.tech_view.overlays.toggle_bollinger()
        elif which == "m":
            self.tech_view.overlays.toggle_ma120()
        elif which == "s":
            self.tech_view.overlays.toggle_supertrend()
        else:
            return
        self._redraw_chart()

    def _redraw_chart(self) -> None:
        content = self.query_one("#content", Container)
        chart = content.query_one(KlineChart) if content.query(KlineChart) else None
        if chart is not None:
            chart.redraw()

    @staticmethod
    def _csv_path_for(symbol: str) -> Path | None:
        """根据 IBKR_SYMBOLS 的 type 字段返回对应 CSV 路径。"""
        for entry in IBKR_SYMBOLS:
            if entry["name"] == symbol:
                kind = entry["type"]
                sub = "stocks" if kind == "stock" else "indices"
                return Path("data") / sub / f"{symbol}.csv"
        return None
