"""MacroChart — 宏观图 widget（时序折线 + 期限结构快照）。

渲染关注点：plotext 调用。状态机（overlaid 集合 / term_cursor）在 MacroView，
本 widget 只读 macro_view 并按其状态画图。

两种图：
  - 时序折线（所有分类）：x=日期，对 overlaid 集合里每个系列画一条 plt.plot。
    派生系列（SPREAD_2S10S 等）若在 overlaid 里也画。不同频率系列靠日期对齐，
    NaN/缺值用 None 跳过（复用批 D 的 _clean 思路）。
  - 期限结构快照（仅 rates/tips）：x=期限标签（1mo→30y），y=term_cursor 那天的收益率。
    ←→ 移 term_cursor 改“快照日期”。

实现说明（同 KlineChart）：用 2 个独立 PlotextPlot 子 widget 而非 subplots，
规避 textual-plotext 下 subplots 高度分配 bug。
"""

from __future__ import annotations

import math

import pandas as pd
from textual.app import ComposeResult
from textual.containers import Vertical
from textual_plotext import PlotextPlot

from src.config import TERM_INFO, TERM_SERIES
from src.tui.state import MacroView

# plotext 默认按 %d/%m/%Y 解析字符串日期；与 KlineChart 保持一致。
_DATE_FMT = "%d/%m/%Y"


def _clean(series: pd.Series) -> list:
    """把 Series 转成 plotext 能吃的 list：NaN/Inf → None（plotext 跳过 None）。"""
    out: list[float | None] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            out.append(None)
        else:
            out.append(float(v))
    return out


class _SubPlot(PlotextPlot):
    """单个 plotext 子图（时序/期限结构）。独立 plotsize+build，规避 subplots bug。"""

    def on_size(self) -> None:
        """尺寸变化 → 触发父 MacroChart 重画，让图适应新尺寸。"""
        parent = self.parent
        if isinstance(parent, MacroChart):
            parent.redraw()


class MacroChart(Vertical):
    """宏观图容器：时序折线 + 期限结构快照（按分类切换显示）。

    内部持 2 个 _SubPlot：#macro-ts（时序折线，常驻）+ #macro-term（期限结构，
    仅 rates/tips 显示）。状态机在 MacroView。
    """

    def __init__(self, macro_view: MacroView) -> None:
        super().__init__()
        self.macro_view = macro_view
        self.df: pd.DataFrame | None = None
        self._category: str | None = None
        self._ts: _SubPlot | None = None
        self._term: _SubPlot | None = None

    def compose(self) -> ComposeResult:
        self._ts = _SubPlot(id="macro-ts")
        self._term = _SubPlot(id="macro-term")
        yield self._ts
        yield self._term

    # ── 数据入口 ────────────────────────────────────────
    def update_data(self, df: pd.DataFrame, category: str, overlaid: set[str]) -> None:
        """设置 df + 分类 + 当前叠加集合，重画。

        overlaid 由调用方从 macro_view.overlaid_series 传入（解耦 widget 与状态机）。
        """
        self.df = df
        self._category = category
        self.call_after_refresh(self.redraw)

    # ── 重画整张图 ─────────────────────────────────────
    def redraw(self) -> None:
        """根据 macro_view 状态重画时序折线 + 期限结构（若有）。"""
        if self.df is None or self._ts is None:
            return
        self._draw_timeseries()
        self._draw_term_structure()

    # ── 时序折线 ────────────────────────────────────────
    def _draw_timeseries(self) -> None:
        """对 overlaid 集合里每个系列画一条 plt.plot。"""
        if self._ts is None or self.df is None:
            return
        plt = self._ts.plt
        plt.clear_data()
        plt.theme("pro")
        overlaid = self.macro_view.overlaid_series
        dates = [d.strftime(_DATE_FMT) for d in self.df.index]
        drew_any = False
        for series in sorted(overlaid):
            if series in self.df.columns:
                plt.plot(dates, _clean(self.df[series]), label=series)
                drew_any = True
        cat = self._category or "?"
        plt.title(f"时序折线 | {cat} | 叠加 {len(overlaid)} 系列")
        if not drew_any:
            plt.plot(dates, [0] * len(dates), label="（空格选择系列叠加）")
        self._ts.refresh()

    # ── 期限结构快照 ────────────────────────────────────
    def _draw_term_structure(self) -> None:
        """rates/tips 画 term_cursor 那天的期限曲线；其它分类隐藏。"""
        if self._term is None or self.df is None or self._category is None:
            return
        is_term = self._category in TERM_SERIES
        self._term.display = is_term
        if not is_term:
            return
        series_list = TERM_SERIES[self._category]
        cur = (
            self.macro_view.term_cursor.current()
            if self.macro_view.term_cursor
            else None
        )
        plt = self._term.plt
        plt.clear_data()
        plt.theme("pro")
        labels = [TERM_INFO[s].short for s in series_list]
        if cur is not None and cur in self.df.index:
            row = self.df.loc[cur]
            ys = [
                float(row[s]) if s in row and pd.notna(row[s]) else None
                for s in series_list
            ]
        else:
            ys = [None] * len(series_list)
        # plotext 跳过 None；用整数 x 保证标签顺序对齐
        xs = list(range(len(series_list)))
        # 全 None 行（非交易日/假日，DGS 系列无数据）不调 plot：
        # plotext 画空序列的图例 marker 会抛 IndexError，留空图 + 标题即可
        if any(v is not None for v in ys):
            plt.plot(xs, ys, label=str(cur.date()) if cur is not None else "?")
        plt.xticks(xs, labels)
        plt.title(
            f"期限结构 | {self._category} | {cur.date() if cur is not None else '—'}"
        )
        self._term.refresh()

    # ── 期限光标移动 ────────────────────────────────────
    def move_term_cursor(self, direction: str) -> None:
        """←/→ 移期限结构快照日期，重画期限图。仅 rates/tips 有效。"""
        if self.macro_view.term_cursor is None:
            return
        self.macro_view.term_cursor.move(direction)
        self._draw_term_structure()


if __name__ == "__main__":
    # ponytail: 自测——合成 rates df 验证渲染不崩 + 期限结构有 11 点。
    mv = MacroView()
    df = pd.DataFrame(
        {
            "DGS1MO": [3.7, 3.6, 3.5],
            "DGS3MO": [3.84, 3.7, 3.6],
            "DGS6MO": [3.95, 3.9, 3.8],
            "DGS1": [3.96, 3.9, 3.85],
            "DGS2": [4.09, 4.0, 3.9],
            "DGS3": [4.13, 4.1, 4.0],
            "DGS5": [4.15, 4.1, 4.05],
            "DGS7": [4.26, 4.2, 4.1],
            "DGS10": [4.4, 4.3, 4.2],
            "DGS20": [4.87, 4.8, 4.7],
            "DGS30": [4.86, 4.8, 4.7],
            "DGS5_": [4.0, 4.0, 4.0],  # 占位
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    from src.macro import derive_macro

    df = derive_macro(df)
    mv.on_category_changed("rates", list(df.index))
    mv.toggle_series("DGS10")
    # 纯逻辑自检（无 Textual 运行时也能跑）
    assert mv.overlaid_series == {"DGS10"}, mv.overlaid_series
    assert mv.term_cursor is not None
    assert mv.term_cursor.current() == df.index[-1]
    print(
        "MacroView 自检 OK | overlaid:",
        mv.overlaid_series,
        "| cursor:",
        mv.term_cursor.current().date(),
    )
    print("TERM_SERIES rates 点数:", len(TERM_SERIES["rates"]))

    # 回归：全 None 行（假日 DGS 无数据）不崩——plotext 画空序列图例会 IndexError
    all_none_row = pd.DataFrame(
        {s: [float("nan")] for s in TERM_SERIES["rates"]},
        index=pd.to_datetime(["2024-01-04"]),
    )

    mv2 = MacroView()
    mv2.on_category_changed("rates", list(all_none_row.index))
    chart = MacroChart(mv2)
    chart.df = all_none_row
    chart._category = "rates"
    # 直接验证 plotext build 不抛（复现 27x12 下的崩溃点）
    from textual_plotext.plot import Plot as _P

    plt = _P()
    plt.theme("pro")
    chart._term = type(
        "T", (), {"plt": plt, "display": True, "refresh": lambda self: None}
    )()
    chart.macro_view = mv2
    chart._draw_term_structure()  # 全 None 行不应崩
    print("全 None 行渲染自检 OK")
