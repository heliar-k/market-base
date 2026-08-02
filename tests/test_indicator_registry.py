"""C5: 指标注册表（INDICATOR_COLUMNS / MA_PERIODS）一致性测试。

注册表定位 = 消费方契约层（analyze/TUI 可见的别名列）：
- 注册表 ⊆ 实际产出：改名/删除列时旧注册名暴露（防幽灵）
- add_smc 产出 ⊆ 注册表：新增 SMC 列忘注册时暴露（防漏注册）
- CDL_* 由 ta 库动态命名，按前缀约定
"""

import pandas as pd

from src.indicators import (
    INDICATOR_COLUMNS,
    MA_PERIODS,
    add_cdl_patterns,
    add_ma,
    add_smc,
    compute_all_indicators,
)


def _sample_df(n: int = 150) -> pd.DataFrame:
    """足够长的 OHLCV 样本（>120 天保证 MA120 可算）。"""
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": [float(i) for i in range(n)],
            "high": [float(i) + 1 for i in range(n)],
            "low": [float(i) - 1 for i in range(n)],
            "close": [float(i) for i in range(n)],
            "volume": [1000.0] * n,
        },
        index=idx,
    )


def test_registry_columns_all_produced():
    df = compute_all_indicators(_sample_df())
    missing = INDICATOR_COLUMNS - set(df.columns)
    assert not missing, f"注册表列未产出（改名漏同步?）: {sorted(missing)}"


def test_smc_columns_registered():
    df = add_smc(_sample_df())
    smc = [c for c in df.columns if c.startswith("SMC_")]
    assert smc, "add_smc 未产出 SMC_* 列"
    unregistered = set(smc) - INDICATOR_COLUMNS
    assert not unregistered, f"新增 SMC 列未注册: {sorted(unregistered)}"


def test_cdl_output_prefix_convention():
    df = add_cdl_patterns(_sample_df())
    cdl = [c for c in df.columns if c.startswith("CDL_")]
    assert cdl, "add_cdl_patterns 未产出 CDL_* 列"
    # 前缀约定：全部 CDL_ 开头（ta 库动态命名，不进注册表）
    assert all(c.startswith("CDL_") for c in cdl)
    assert len(cdl) >= 10  # 不绑死 62，ta 版本增减形态不误报


def test_ma_periods_match_add_ma_default():
    df = add_ma(_sample_df())
    produced = set(c for c in df.columns if c.startswith("MA"))
    assert produced == {f"MA{p}" for p in MA_PERIODS}
