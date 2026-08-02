"""KlineChart — 技术分析 K 线图 widget（candlestick + 主图叠加 + 2 副图 + vline 回看）。

渲染关注点：窗口滚动 + plotext 调用。状态机（cursor/slots/overlays）在 TechView，
本 widget 只读 tech_view 并按其状态画图。回看交互的纯逻辑见 visible_window。

实现说明：用 3 个独立 PlotextPlot 子 widget（主图/副图1/副图2）而非 plotext subplots。
原因：plotext 5.3.2 的 subplots 在 textual-plotext 的 render()（plotsize→build）流程下，
高度分配 bug 导致只有最后一个 subplot 渲染。独立 widget 各自 plotsize+build 不受影响。
代价：副图与主图 x 轴对齐靠相同的 xlim（时间范围一致），不靠 plotext 共享轴。
"""

from __future__ import annotations

import math

import pandas as pd
from textual.app import ComposeResult
from textual.containers import Vertical
from textual_plotext import PlotextPlot

from src.tui.state import TechView

# plotext 默认按 %d/%m/%Y 解析字符串日期；统一用这个格式喂给 candlestick/vline/plot。
_DATE_FMT = "%d/%m/%Y"
# 终端渲染分辨率有限，主图默认只展示最近 N 根 K 线（过多会挤压成糊）。
_DEFAULT_WINDOW = 200

# ── 统一色板 ──
_CLR_UP = "green"  # 阳线绿色
_CLR_DOWN = "red"  # 阴线红色
_CLR_VLINE = "gray"  # 回看光标线
_CLR_HLINE = "gray"  # 副图参考线（70/30 等）
_CLR_MA = {5: "cyan", 10: "blue", 20: "magenta", 60: "yellow", 120: "white"}
_CLR_BB = {"BB上": "yellow", "BB中": "white", "BB下": "yellow"}
_CLR_SUPERTREND = "green"
_CLR_MACD = "cyan"
_CLR_MACD_SIGNAL = "magenta"
_CLR_STOCH_K = "cyan"
_CLR_STOCH_D = "magenta"
_CLR_RSI = "cyan"
_CLR_CCI = "cyan"
_CLR_MFI = "cyan"


def visible_window(
    dates: list[pd.Timestamp],
    cursor_idx: int,
    size: int = _DEFAULT_WINDOW,
) -> slice:
    """计算 cursor 周围的可见窗口索引范围（纯逻辑）。

    规则（ADR-0001 决策3）：
      - 数据少于 size → 窗口覆盖全部 [0, len]。
      - cursor 在当前窗口（最近 size 根）内 → 窗口不动（停在最近 size 根）。
      - cursor 移出窗口 → 滚动使 cursor 可见：cursor 在右外则窗口右端=cursor+1；
        cursor 在左外则窗口左端=cursor，再 clamp 到 [0, len]。
      - 始终保证 cursor 落进 [start, stop) 且窗口不越过数据边界。
    """
    n = len(dates)
    if n == 0:
        return slice(0, 0)
    if n <= size:
        return slice(0, n)
    # 初始/默认窗口：最后 size 根
    start = n - size
    stop = n
    if start <= cursor_idx < stop:
        # cursor 在当前窗口内 → 不动
        return slice(start, stop)
    if cursor_idx >= stop:
        # cursor 在右外 → 滚到 cursor 可见，窗口右端 = cursor+1，再 clamp
        stop = min(cursor_idx + 1, n)
        start = max(0, stop - size)
    else:  # cursor_idx < start，在左外
        start = max(0, cursor_idx)
        stop = min(n, start + size)
    return slice(start, stop)


def _clean(series: pd.Series) -> list:
    """把指标 Series 转成 plotext 能吃的 list：NaN/Inf → None（plotext 跳过 None）。"""
    out: list[float | None] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            out.append(None)
        else:
            out.append(float(v))
    return out


class _SubPlot(PlotextPlot):
    """单个 plotext 子图（主图或副图）。独立 plotsize+build，规避 subplots 高度 bug。"""

    def __init__(self, plot_id: str) -> None:
        super().__init__(id=plot_id)

    def on_size(self) -> None:
        """尺寸变化（含终端 resize）→ 触发父 KlineChart 重画，让图适应新尺寸。"""
        parent = self.parent
        if isinstance(parent, KlineChart):
            parent.redraw()


class KlineChart(Vertical):
    """K 线图容器：主图 candlestick + 叠加 + 2 副图 + vline 回看标记。

    内部持有 3 个 _SubPlot（PlotextPlot）。状态机（cursor/slots/overlays）在 TechView。
    """

    def __init__(self, tech_view: TechView) -> None:
        super().__init__()
        self.tech_view = tech_view
        self.df: pd.DataFrame | None = None
        self._window: slice | None = None
        self._main: _SubPlot | None = None
        self._sub1: _SubPlot | None = None
        self._sub2: _SubPlot | None = None

    def compose(self) -> ComposeResult:
        self._main = _SubPlot("kline-main")
        self._sub1 = _SubPlot("kline-sub1")
        self._sub2 = _SubPlot("kline-sub2")
        yield self._main
        yield self._sub1
        yield self._sub2

    # ── 数据入口 ────────────────────────────────────────
    def update_data(self, df: pd.DataFrame) -> None:
        """设置 df（带指标列）并重画。切标的时调。

        用 call_after_refresh 推后 redraw：mount 后立即调 update_data 时，
        compose 尚未跑完（_main 为 None），直接 redraw 会跳过。推后到
        下一帧布局完成后，_main 已就绪、子图也有真实尺寸。
        """
        self.df = df
        self._window = None  # 重置窗口，下次 redraw 用默认（最近 size 根）
        self.call_after_refresh(self.redraw)

    # ── 重画整张图（主图+副图+vline）────────────────────────────────────
    def redraw(self) -> None:
        """根据 tech_view 状态重画 3 个子图。"""
        if self.df is None or self.df.empty:
            return
        if self._main is None:
            return  # 未 mount
        self._draw_main()
        self._draw_subplot(self._sub1, self.tech_view.slots.slot1)
        self._draw_subplot(self._sub2, self.tech_view.slots.slot2)

    # ── 主图 ────────────────────────────────────────────────────────────
    def _draw_main(self) -> None:
        plt = self._main.plt
        plt.clear_data()
        plt.theme("pro")  # 专业暗色主题
        win = self._current_window()
        sub = self.df.iloc[win]
        dates = [d.strftime(_DATE_FMT) for d in sub.index]
        plt.candlestick(
            dates,
            {
                "Open": sub["open"].tolist(),
                "Close": sub["close"].tolist(),
                "High": sub["high"].tolist(),
                "Low": sub["low"].tolist(),
            },
            colors=(_CLR_UP, _CLR_DOWN),
        )
        # 叠加层：MA 项从 overlays 派生（与 OverlayToggles 同源，不硬枚举）
        # isdigit 守卫防未来 overlay 项（如 "MACD"）误入 MA 分支
        overlays = set(self.tech_view.overlays.active_overlays())
        for col in sorted(
            (c for c in overlays if c.startswith("MA") and c[2:].isdigit()),
            key=lambda c: int(c[2:]),
        ):
            if col not in sub.columns:
                continue
            plt.plot(
                dates,
                _clean(sub[col]),
                label=col,
                color=_CLR_MA.get(int(col[2:]), "white"),
            )
        if "BB" in overlays:
            for col, lab in (
                ("BB_upper", "BB上"),
                ("BB_mid", "BB中"),
                ("BB_lower", "BB下"),
            ):
                if col in sub.columns:
                    plt.plot(
                        dates,
                        _clean(sub[col]),
                        label=lab,
                        color=_CLR_BB.get(lab, "white"),
                    )
        if "SuperTrend" in overlays and "SUPERT" in sub.columns:
            plt.plot(
                dates,
                _clean(sub["SUPERT"]),
                label="SuperTrend",
                color=_CLR_SUPERTREND,
            )
        plt.title(f"K线 (主图) | {self._symbol_label()}")
        # cursor vline
        cur = self.tech_view.cursor.current()
        if cur is not None:
            plt.vline(cur.strftime(_DATE_FMT), color=_CLR_VLINE)
        self._main.refresh()

    def _symbol_label(self) -> str:
        """主图标题里的标的/日期提示。"""
        cur = self.tech_view.cursor.current()
        return str(cur.date()) if cur is not None else ""

    # ── 副图 ────────────────────────────────────────────────────────────
    def _draw_subplot(self, plot: _SubPlot | None, indicator: str) -> None:
        if plot is None:
            return
        plt = plot.plt
        plt.clear_data()
        plt.theme("pro")
        win = self._current_window()
        sub = self.df.iloc[win]
        dates = [d.strftime(_DATE_FMT) for d in sub.index]
        if indicator == "RSI":
            plt.title("RSI")
            if "RSI" in sub.columns:
                plt.plot(dates, _clean(sub["RSI"]), label="RSI", color=_CLR_RSI)
            plt.hline(70, color=_CLR_HLINE)
            plt.hline(30, color=_CLR_HLINE)
        elif indicator == "MACD":
            plt.title("MACD")
            if "MACD_hist" in sub.columns:
                plt.bar(dates, _clean(sub["MACD_hist"]), label="hist")
            if "MACD" in sub.columns:
                plt.plot(dates, _clean(sub["MACD"]), label="MACD", color=_CLR_MACD)
            if "MACD_signal" in sub.columns:
                plt.plot(
                    dates,
                    _clean(sub["MACD_signal"]),
                    label="signal",
                    color=_CLR_MACD_SIGNAL,
                )
        elif indicator == "Stoch":
            plt.title("Stoch")
            if "STOCH_k" in sub.columns:
                plt.plot(
                    dates,
                    _clean(sub["STOCH_k"]),
                    label="%K",
                    color=_CLR_STOCH_K,
                )
            if "STOCH_d" in sub.columns:
                plt.plot(
                    dates,
                    _clean(sub["STOCH_d"]),
                    label="%D",
                    color=_CLR_STOCH_D,
                )
            plt.hline(80, color=_CLR_HLINE)
            plt.hline(20, color=_CLR_HLINE)
        elif indicator == "CCI":
            plt.title("CCI")
            if "CCI" in sub.columns:
                plt.plot(dates, _clean(sub["CCI"]), label="CCI", color=_CLR_CCI)
            plt.hline(100, color=_CLR_HLINE)
            plt.hline(-100, color=_CLR_HLINE)
        elif indicator == "MFI":
            plt.title("MFI")
            if "MFI" in sub.columns:
                plt.plot(dates, _clean(sub["MFI"]), label="MFI", color=_CLR_MFI)
            plt.hline(80, color=_CLR_HLINE)
            plt.hline(20, color=_CLR_HLINE)
        plot.refresh()

    # ── 窗口管理 ────────────────────────────────────────────────────────
    def _current_window(self) -> slice:
        """返回当前显示窗口 slice；按 visible_window 算（cursor 在内则不动）。"""
        df = self.df
        if df is None or df.empty:
            return slice(0, 0)
        dates = list(df.index)
        cur = self.tech_view.cursor.current()
        if cur is None:
            idx = len(dates) - 1
        else:
            idx = dates.index(cur)
        win = visible_window(dates, idx)
        self._window = win
        return win

    # ── 轻量 cursor 移动 ────────────────────────────────────────────────
    def move_cursor(self, direction: str) -> None:
        """←/→ 移动光标，重画整图（vline 跟手 + 窗口滚动 + 副图跟随）。

        决策点5：vline 实时跟手重画（轻），侧栏 analyze 防抖 50ms（由 screen 负责）。
        """
        if direction == "left":
            self.tech_view.cursor.move_left()
        elif direction == "right":
            self.tech_view.cursor.move_right()
        else:
            return
        self.redraw()
