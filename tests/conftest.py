"""共享测试夹具。"""

import shutil
from pathlib import Path

import pandas as pd
import pytest

# TUI 冒烟测试会调 load_or_compute 写真实 data/cache/；测试后清理避免污染工作区。
_CACHE_DIR = Path("data/cache")


@pytest.fixture(autouse=True)
def _clean_test_cache():
    """每个测试后清 data/cache/（测试可重建的缓存，非用户数据）。"""
    yield
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR)


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """构造一小段 OHLCV 数据用于指标/分析测试。

    不依赖真实 CSV 文件，保证测试可重复且无未来函数污染。
    """
    rows = [
        ("2024-01-01", 100.0, 102.0, 99.0, 101.0, 1_000_000),
        ("2024-01-02", 101.0, 103.5, 100.5, 103.0, 1_100_000),
        ("2024-01-03", 103.0, 104.0, 102.0, 102.5, 900_000),
        ("2024-01-04", 102.5, 105.0, 102.0, 104.5, 1_200_000),
        ("2024-01-05", 104.5, 106.0, 104.0, 105.5, 1_300_000),
        ("2024-01-06", 105.5, 107.0, 105.0, 106.5, 1_150_000),
        ("2024-01-07", 106.5, 108.0, 106.0, 107.0, 1_250_000),
        ("2024-01-08", 107.0, 109.0, 106.5, 108.5, 1_400_000),
        ("2024-01-09", 108.5, 110.0, 108.0, 109.5, 1_350_000),
        ("2024-01-10", 109.5, 111.0, 109.0, 110.5, 1_500_000),
        ("2024-01-11", 110.5, 112.0, 110.0, 111.0, 1_200_000),
        ("2024-01-12", 111.0, 113.0, 110.5, 112.5, 1_300_000),
        ("2024-01-13", 112.5, 114.0, 112.0, 113.5, 1_400_000),
        ("2024-01-14", 113.5, 115.0, 113.0, 114.5, 1_350_000),
        ("2024-01-15", 114.5, 116.0, 114.0, 115.5, 1_250_000),
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


@pytest.fixture
def real_aapl_csv() -> Path | None:
    """真实 AAPL CSV，存在则返回路径，否则 None。

    用于可选的集成测试；缺数据时测试自动跳过。
    """
    p = Path("data/stocks/AAPL.csv")
    return p if p.exists() else None
