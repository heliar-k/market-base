"""TUI 状态机纯逻辑测试 — 不依赖 Textual。"""

from src.tui.state import Mode, TuiState


def test_default_mode_is_tech() -> None:
    """新建状态机默认在技术分析模式。"""
    state = TuiState()
    assert state.mode == Mode.TECH


def test_switch_mode_changes_mode() -> None:
    """switch_mode 切换当前模式。"""
    state = TuiState()
    state.switch_mode(Mode.MACRO)
    assert state.mode == Mode.MACRO


def test_select_tech_sets_selection() -> None:
    """select_tech 设置 tech_selected。"""
    state = TuiState()
    state.select_tech("AAPL")
    assert state.tech_selected == "AAPL"


def test_switch_mode_preserves_tech_selection() -> None:
    """切到宏观再切回技术分析，tech_selected 仍在（选中态保留）。"""
    state = TuiState()
    state.select_tech("AAPL")
    state.switch_mode(Mode.MACRO)
    assert state.tech_selected == "AAPL"  # 切走不清
    state.switch_mode(Mode.TECH)
    assert state.tech_selected == "AAPL"  # 切回来还在


def test_select_macro_sets_category_and_series() -> None:
    """select_macro 同时设置 macro_category 和 macro_series。"""
    state = TuiState()
    state.select_macro("rates", "DGS10")
    assert state.macro_category == "rates"
    assert state.macro_series == "DGS10"


def test_current_selection_returns_symbol_in_tech_mode() -> None:
    """tech 模式下 current_selection 返回 symbol str。"""
    state = TuiState()
    state.select_tech("AAPL")
    assert state.current_selection() == "AAPL"


def test_current_selection_returns_tuple_in_macro_mode() -> None:
    """macro 模式下 current_selection 返回 (category, series) tuple。"""
    state = TuiState()
    state.select_macro("rates", "DGS10")
    state.switch_mode(Mode.MACRO)
    assert state.current_selection() == ("rates", "DGS10")


def test_switch_to_macro_does_not_clear_tech_selection() -> None:
    """切到宏观不清技术分析的选中态（双向保留）。"""
    state = TuiState()
    state.select_tech("AAPL")
    state.select_macro("rates", "DGS10")
    state.switch_mode(Mode.MACRO)
    # 切到宏观后，技术分析选中态仍在
    assert state.tech_selected == "AAPL"
    # 反过来：在宏观选中后切到技术分析，宏观选中态也在
    state.switch_mode(Mode.TECH)
    assert state.macro_category == "rates"
    assert state.macro_series == "DGS10"
