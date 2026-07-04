"""TUI 状态机：当前模式 + 各模式选中态。纯逻辑，不依赖 Textual。"""

from dataclasses import dataclass


class Mode:
    """TUI 两种模式标识。"""

    TECH = "tech"
    MACRO = "macro"


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
