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
from src.config import FRED_SERIES, IBKR_SYMBOLS
from src.tui.state import MacroView, Mode, TechView, TuiState
from src.tui.widgets.diag_sidebar import DiagSidebar
from src.tui.widgets.kline_chart import KlineChart
from src.tui.widgets.macro_chart import MacroChart

# 回看侧栏 analyze 防抖延迟（决策点5）：连续按 ← 时只在停顿后刷新诊断。
_DEBOUNCE_SECONDS = 0.05


class StatusBar(Static):
    """底部状态栏：当前模式 + 操作提示。"""

    def render_text(self, mode: str) -> None:
        if mode == Mode.MACRO:
            self.update(
                "[bold]MACRO[/]  │  [dim]空格[/]叠加  [dim]←→[/]期限光标  "
                "[dim]Tab[/]切模式  [dim]q[/]退出"
            )
        else:
            self.update(
                "[bold]TECH[/]  │  [dim]←→[/]回看  [dim]1/2[/]副图  "
                "[dim]b/m/s[/]叠加  [dim]Tab[/]切模式  [dim]q[/]退出"
            )


class PanelHeader(Static):
    """面板标题栏：带背景色的标题行。"""

    def __init__(self, title: str, id: str | None = None) -> None:  # noqa: A002
        super().__init__(f"[bold]{title}[/]", id=id)
        self.add_class("panel-header")


class TechListView(ListView):
    """技术分析模式侧栏：平铺所有 IBKR 标的。"""

    def __init__(self) -> None:
        super().__init__(*[ListItem(Static(s["name"])) for s in IBKR_SYMBOLS])


class MacroTree(Tree):
    """宏观模式侧栏：两级树 分类→系列（原始 + 同分类派生 + 跨分类派生）。"""

    def __init__(self) -> None:
        from src.macro import (
            cross_category_series_for,
            derived_series_for_category,
        )

        super().__init__("FRED", id="macro-tree")
        for category, series_map in FRED_SERIES.items():
            node = self.root.add(category, allow_expand=True)
            for metric in series_map:
                node.add_leaf(metric)
            # 同分类派生系列（输入列都在本分类）
            for derived in derived_series_for_category(category):
                node.add_leaf(derived)
            # 跨分类派生系列（如 BEI 横跨 rates+tips，两分类下都列出）
            for derived in cross_category_series_for(category):
                node.add_leaf(derived)


class MainScreen(Container):
    """主屏：三栏 + 模式切换。

    状态逻辑委托给 TuiState（模式/选中态）+ TechView（技术分析视图状态）。
    widget 只做渲染 + 把事件翻译成状态调用。
    """

    CSS = """
    MainScreen {
        layout: vertical;
        background: #0d1117;
    }

    #body {
        height: 1fr;
    }

    /* ── 侧栏面板 ── */
    #sidebar-container {
        width: 24;
        layout: vertical;
        background: #161b22;
        border-right: solid #21262d;
    }
    #sidebar {
        height: 1fr;
        overflow: auto;
    }

    /* ── 内容区 ── */
    #content {
        background: #0d1117;
    }
    #content-text {
        padding: 1 2;
        color: #484f58;
        text-style: italic;
    }

    /* ── 诊断面板 ── */
    #diag-container {
        width: 36;
        layout: vertical;
        background: #161b22;
        border-left: solid #21262d;
    }
    #diag {
        height: 1fr;
        overflow: auto;
    }
    #diag-text {
        padding: 0 1;
        color: #c9d1d9;
    }

    /* ── 面板标题栏 ── */
    .panel-header {
        height: 1;
        background: #21262d;
        color: #58a6ff;
        padding: 0 1;
        text-style: bold;
    }

    /* ── 状态栏 ── */
    #statusbar {
        height: 1;
        background: #161b22;
        color: #8b949e;
        padding: 0 1;
        dock: bottom;
        border-top: solid #21262d;
    }

    /* ── K 线图三层 ── */
    KlineChart { height: 1fr; }
    KlineChart > PlotextPlot { width: 1fr; }
    KlineChart > #kline-main { height: 3fr; }
    KlineChart > #kline-sub1 { height: 1fr; }
    KlineChart > #kline-sub2 { height: 1fr; }

    /* ── 宏观图 ── */
    MacroChart { height: 1fr; }
    MacroChart > PlotextPlot { width: 1fr; }
    MacroChart > #macro-ts { height: 2fr; }
    MacroChart > #macro-term { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.state = TuiState()
        self.tech_view = TechView()
        self.macro_view = MacroView()
        self.content_text = ""  # 宏观模式/无数据时的可观测文本
        self._current_df: pd.DataFrame | None = None  # 当前加载的 df（带指标）
        self._current_symbol: str | None = None
        self._macro_df: pd.DataFrame | None = None  # 宏观当前分类 df（带派生列）
        self._macro_category_loaded: str | None = None  # 已加载到 macro_view 的分类
        self._debounce_timer = None  # 侧栏防抖定时器

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Container(id="sidebar-container"):
                yield PanelHeader("📊 标的列表", id="sidebar-header")
                yield Container(TechListView(), id="sidebar")
            yield Container(Static("选择标的以加载", id="content-text"), id="content")
            with Container(id="diag-container"):
                yield PanelHeader("📋 诊断分析", id="diag-header")
                yield Container(DiagSidebar("等待诊断", id="diag-text"), id="diag")
        yield StatusBar("TECH | ←→回看 | Tab切模式 | q退出", id="statusbar")

    # ── 模式切换：swap 侧栏 widget ──────────────────────────────────────
    async def swap_sidebar(self) -> None:
        """按当前 state.mode 把侧栏换成 ListView / Tree。"""
        sidebar = self.query_one("#sidebar", Container)
        for child in list(sidebar.children):
            await child.remove()
        # 更新标题
        header = self.query_one("#sidebar-header", PanelHeader)
        if self.state.mode == Mode.TECH:
            header.update("[bold]📊 标的列表[/]")
            await sidebar.mount(TechListView())
        else:
            header.update("[bold]🌐 宏观数据[/]")
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
            # 分类变化 → 重置叠加集合 + 期限光标，并（重新）加载数据
            if category != self.macro_view.category:
                self._macro_df = None
                self._macro_category_loaded = None
            self._load_macro_worker(category)
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

    # ── 宏观：Worker 化加载 FRED 分类（macro.load_macro_category）─────────
    @work(thread=True, exclusive=True, name="macro-load")
    def _load_macro_worker(self, category: str) -> None:
        """后台加载 FRED 分类：伙伴合并 + 派生列（含单位归一化）由 loader 负责。"""
        from src.macro import load_macro_category

        self.app.call_from_thread(
            self.query_one("#content-text", Static).update, f"{category} 加载中..."
        )
        try:
            df = load_macro_category(category)
        except FileNotFoundError:
            self.app.call_from_thread(self._show_macro_no_data, category)
            return
        self.app.call_from_thread(self._on_macro_loaded, category, df)

    def _show_macro_no_data(self, category: str) -> None:
        msg = f"{category}：无数据，请先 ./bin/fetch_fred"
        self.content_text = msg
        self._macro_df = None
        placeholder = self.query_one("#content-text", Static)
        placeholder.display = True
        placeholder.update(msg)
        chart = self._macro_chart_or_none()
        if chart is not None:
            chart.display = False

    def _macro_chart_or_none(self) -> MacroChart | None:
        content = self.query_one("#content", Container)
        return content.query_one(MacroChart) if content.query(MacroChart) else None

    def _on_macro_loaded(self, category: str, df: pd.DataFrame) -> None:
        """宏观 df 就绪：分类变化时重置状态 + 挂载/更新 MacroChart。"""
        self._macro_df = df
        self._macro_category_loaded = category
        if category != self.macro_view.category:
            self.macro_view.on_category_changed(category, list(df.index))
        # 挂载 MacroChart（或复用已有）
        content = self.query_one("#content", Container)
        chart = self._macro_chart_or_none()
        if chart is None:
            chart = MacroChart(self.macro_view)
            placeholder = self.query_one("#content-text", Static)
            placeholder.display = False
            content.mount(chart)
        chart.display = True
        chart.update_data(df, category, set(self.macro_view.overlaid_series))

    # ── 宏观：空格 toggle 叠加 + ←→ 期限光标 ────────────────────────────
    def toggle_macro_series(self) -> None:
        """空格 toggle 当前选中系列到叠加集合，重画时序折线。"""
        if self.state.macro_series is None or self._macro_df is None:
            return
        self.macro_view.toggle_series(self.state.macro_series)
        chart = self._macro_chart_or_none()
        if chart is not None:
            chart.redraw()

    def move_term_cursor(self, direction: str) -> None:
        """←/→ 移期限结构快照日期（仅 rates/tips），重画期限图。"""
        chart = self._macro_chart_or_none()
        if chart is not None:
            chart.move_term_cursor(direction)
