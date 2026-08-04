"""plotext 子图共享件：_clean 清洗 + _SubPlot 基类（KlineChart/MacroChart 共用）。"""

from __future__ import annotations

import math

import pandas as pd
from textual_plotext import PlotextPlot


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
    """单个 plotext 子图。独立 plotsize+build，规避 subplots 高度分配 bug。"""

    def on_size(self) -> None:
        """尺寸变化（含终端 resize）→ 触发父图重画，让图适应新尺寸。"""
        # ponytail: 鸭子类型，父 widget（KlineChart/MacroChart）都有 redraw
        if hasattr(self.parent, "redraw"):
            self.parent.redraw()
