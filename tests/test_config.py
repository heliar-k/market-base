"""config.py 标签映射不变量测试。

防止 TERM_INFO / TERM_SERIES / _MACRO_LABELS 之间漂移：
- 期限品种必须全部登记标签（漏登记 → x 轴渲染原始 series id）
- TERM_INFO 不允许残留过期条目（已从 TERM_SERIES 移除的品种）
- 抽查关键标签值，防止“后缀启发式”死灰复燃
"""

from src.config import TERM_INFO, TERM_SERIES


def _all_term_series() -> list[str]:
    return [s for lst in TERM_SERIES.values() for s in lst]


def test_term_info_covers_all_term_series():
    """TERM_SERIES 里每个系列都必须有展示信息（长名 + 短标签）。"""
    missing = [s for s in _all_term_series() if s not in TERM_INFO]
    assert missing == [], f"TERM_SERIES 中未登记 TERM_INFO 的系列: {missing}"


def test_term_info_has_no_stale_entries():
    """TERM_INFO 不允许包含已不在 TERM_SERIES 中的过期系列。"""
    known = set(_all_term_series())
    stale = [k for k in TERM_INFO if k not in known]
    assert stale == [], f"TERM_INFO 中的过期系列（已不在 TERM_SERIES）: {stale}"


def test_term_labels_spot_check():
    """抽查关键期限标签（短标签必须带单位，与 TUI 一致）。"""
    assert TERM_INFO["DGS10"].short == "10y"
    assert TERM_INFO["DGS1MO"].short == "1mo"
    assert TERM_INFO["DGS1"].short == "1y"
    assert TERM_INFO["DFII10"].short == "10y"
    assert TERM_INFO["DGS10"].name == "10年期国债收益率"
    assert TERM_INFO["DFII10"].name == "10年期TIPS收益率"


def test_server_macro_labels_merge():
    """server 的 _MACRO_LABELS 合并了 TERM_INFO 长名，期限品种不应再单独维护。"""
    from src.server import _MACRO_LABELS

    for k, info in TERM_INFO.items():
        assert _MACRO_LABELS[k] == info.name, f"server 标签与 TERM_INFO 不一致: {k}"


def test_fed_fallback_range_shape():
    """兜底目标区间必须是 25bp 网格上的合法区间。"""
    from src.config import FED_TARGET_RANGE_FALLBACK

    lo, hi = FED_TARGET_RANGE_FALLBACK
    assert hi - lo == 0.25
    assert round(lo * 4) == lo * 4  # 落在 25bp 网格上
