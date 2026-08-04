"""TUI 状态机：当前模式 + 各模式选中态。纯逻辑，不依赖 Textual。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import pandas as pd


class Mode:
    """TUI 两种模式标识。"""

    TECH = "tech"
    MACRO = "macro"


@dataclass
class LookbackCursor:
    """回看光标：在 df 的日期索引范围内移动。纯逻辑。

    cursor_date 初始 = dates[-1]（最后一根 K 线）。
    ← 往前一根，→ 往后一根，clamp 到首尾不越界。
    按索引移动（交易日有缺口，按索引才对应“一根 K 线”）。
    """

    dates: list[pd.Timestamp]
    cursor_date: pd.Timestamp | None = None

    def current(self) -> pd.Timestamp | None:
        """当前光标日期；None 时返回 dates[-1]，空列表返回 None。"""
        if not self.dates:
            return None
        if self.cursor_date is None:
            return self.dates[-1]
        return self.cursor_date

    def move(self, direction: str) -> None:
        """按方向字符串分派（left/right），无效方向不动。唯一分派点。"""
        if direction == "left":
            self.move_left()
        elif direction == "right":
            self.move_right()

    def move_left(self, steps: int = 1) -> None:
        """往前移 steps 根，不越过 dates[0]。"""
        if not self.dates:
            return
        idx = self.index() - steps
        self.cursor_date = self.dates[max(idx, 0)]

    def index(self) -> int:
        """当前光标在 dates 中的索引（空 dates 返回 -1）。"""
        cur = self.current()
        return self.dates.index(cur) if cur is not None else len(self.dates) - 1

    def move_right(self, steps: int = 1) -> None:
        """往后移 steps 根，不越过 dates[-1]。"""
        if not self.dates:
            return
        idx = self.index() + steps
        self.cursor_date = self.dates[min(idx, len(self.dates) - 1)]


@dataclass
class SubplotSlots:
    """副图 2 槽位：管理哪两个指标画在副图。纯逻辑。

    默认 slot1=RSI, slot2=MACD。可轮换，候选池 5 选 2，两槽位永不同值。
    """

    CANDIDATES: ClassVar[tuple] = ("RSI", "MACD", "Stoch", "CCI", "MFI")
    slot1: str = "RSI"
    slot2: str = "MACD"

    def cycle_slot1(self) -> None:
        """槽位1 轮换到下一个候选（跳过当前 slot2 占用的）。"""
        self.slot1 = self._next(self.slot1, self.slot2)

    def cycle_slot2(self) -> None:
        """槽位2 轮换到下一个候选（跳过当前 slot1 占用的）。"""
        self.slot2 = self._next(self.slot2, self.slot1)

    def _next(self, current: str, blocked: str) -> str:
        """从 current 轮到下一个非 blocked 的候选。"""
        i = self.CANDIDATES.index(current)
        for step in range(1, len(self.CANDIDATES) + 1):
            cand = self.CANDIDATES[(i + step) % len(self.CANDIDATES)]
            if cand != blocked:
                return cand
        return current  # ponytail: 理论不可达，CANDIDATES 长度 ≥ 2


@dataclass
class OverlayToggles:
    """主图叠加层开关。纯逻辑。

    MA5/10/20/60 + 布林带默认开；MA120 + SuperTrend 默认关。
    """

    ma: tuple = (5, 10, 20, 60)
    ma120: bool = False
    bollinger: bool = True
    supertrend: bool = False

    def toggle_ma120(self) -> None:
        """切换 MA120 叠加。"""
        self.ma120 = not self.ma120

    def toggle_bollinger(self) -> None:
        """切换布林带叠加。"""
        self.bollinger = not self.bollinger

    def toggle_supertrend(self) -> None:
        """切换 SuperTrend 叠加。"""
        self.supertrend = not self.supertrend

    def active_overlays(self) -> list[str]:
        """返回当前开启的叠加层标识列表，批 D 据此画哪些层。"""
        out = [f"MA{p}" for p in self.ma]
        if self.bollinger:
            out.append("BB")
        if self.ma120:
            out.append("MA120")
        if self.supertrend:
            out.append("SuperTrend")
        return out


@dataclass
class MacroView:
    """宏观视图状态：选中分类 + 叠加系列集合 + 期限结构光标。纯逻辑。

    - overlaid_series：空格 toggle 加入/移出的系列名集合（时序折线叠加层）。
    - term_cursor：仅 rates/tips 有期限结构时才建（复用 LookbackCursor），
      ←→ 移动“期限结构快照的日期”。其它分类为 None。
    """

    category: str | None = None
    overlaid_series: set[str] = field(default_factory=set)
    term_cursor: LookbackCursor | None = None

    def toggle_series(self, series: str) -> None:
        """空格 toggle：系列在集合里则移除，不在则加入。"""
        if series in self.overlaid_series:
            self.overlaid_series.discard(series)
        else:
            self.overlaid_series.add(series)

    def on_category_changed(self, category: str, dates: list[pd.Timestamp]) -> None:
        """切分类：更新 category + 清空叠加集合 + 重建 term_cursor（仅期限分类）。"""
        self.category = category
        self.overlaid_series.clear()
        # ponytail: 复用 LookbackCursor，仅 rates/tips 建；其它分类不画期限结构。
        from src.config import TERM_SERIES

        if category in TERM_SERIES:
            self.term_cursor = LookbackCursor(dates=dates)
        else:
            self.term_cursor = None


@dataclass
class TuiState:
    """TUI 状态机：当前模式 + 各模式选中态。

    切换模式时不重置选中态——切回来还在（质询钉死的决策）。
    """

    mode: str = Mode.TECH
    tech_selected: str | None = None
    macro_category: str | None = None
    macro_series: str | None = None

    def switch_mode(self, mode: str) -> None:
        """切换当前模式（不重置选中态）。"""
        self.mode = mode

    def select_tech(self, symbol: str) -> None:
        """选中技术分析模式的标的。"""
        self.tech_selected = symbol

    def select_macro(self, category: str, series: str) -> None:
        """选中宏观模式的分类+系列。"""
        self.macro_category = category
        self.macro_series = series

    def current_selection(self) -> str | tuple[str, str] | None:
        """返回当前模式选中项：tech→symbol str；macro→(category, series)。"""
        if self.mode == Mode.TECH:
            return self.tech_selected
        return (self.macro_category, self.macro_series) if self.macro_category else None


@dataclass
class TechView:
    """技术分析视图状态：聚合回看光标 + 副图槽位 + 主图叠加开关。纯逻辑。

    与 TuiState 分离：TuiState 专注模式/选中态，TechView 专注技术分析视图状态。
    批 D 接线时由 app 持有，切标的时调 on_symbol_changed 重置 cursor。
    """

    cursor: LookbackCursor = field(default_factory=lambda: LookbackCursor(dates=[]))
    slots: SubplotSlots = field(default_factory=SubplotSlots)
    overlays: OverlayToggles = field(default_factory=OverlayToggles)

    def on_symbol_changed(self, dates: list[pd.Timestamp]) -> None:
        """切标的：重建 cursor 到新 df 的末尾。"""
        self.cursor = LookbackCursor(dates=dates)
