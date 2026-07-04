"""TUI 指标缓存层测试。"""

import os
from pathlib import Path

import pandas as pd
import pytest

from src.cache import cache_path, clear_cache, is_cache_fresh, load_or_compute


@pytest.fixture
def small_csv(tmp_path: Path) -> Path:
    """构造一个 15 行 OHLCV CSV 到 tmp_path，够算 MA5/RSI14。"""
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
    csv = tmp_path / "AAPL.csv"
    df.to_csv(csv, index=False)
    return csv


def test_cache_path_returns_expected_location() -> None:
    """cache_path 返回 data/cache/{SYMBOL}_indicators.parquet。"""
    assert cache_path("AAPL") == Path("data/cache/AAPL_indicators.parquet")


def test_is_cache_fresh_false_when_cache_absent(
    small_csv: Path, tmp_path: Path
) -> None:
    """缓存文件不存在时 is_cache_fresh 返回 False。"""
    assert is_cache_fresh("AAPL", small_csv, data_dir=str(tmp_path / "data")) is False


def test_load_or_compute_creates_cache_when_absent(
    small_csv: Path, tmp_path: Path
) -> None:
    """无缓存时：读 CSV + 算指标 + 写 parquet + 返回含指标列的 df。"""
    data_dir = str(tmp_path / "data")
    df = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    assert "MA5" in df.columns
    assert cache_path("AAPL", data_dir).exists()


def test_load_or_compute_reads_cache_when_fresh(
    small_csv: Path, tmp_path: Path
) -> None:
    """缓存新鲜时第二次调用读 parquet，返回与首次相等的 df（忽略 attrs）。"""
    data_dir = str(tmp_path / "data")
    first = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    second = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    pd.testing.assert_frame_equal(first, second)


def test_is_cache_fresh_reflects_mtime_order(small_csv: Path, tmp_path: Path) -> None:
    """缓存 mtime >= csv mtime → True；缓存 mtime < csv mtime → False。"""
    data_dir = str(tmp_path / "data")
    # 先生成缓存
    load_or_compute("AAPL", small_csv, data_dir=data_dir)
    csv_mtime = small_csv.stat().st_mtime
    cache = cache_path("AAPL", data_dir)

    # 缓存晚于 csv → 新鲜
    os.utime(cache, (csv_mtime + 100, csv_mtime + 100))
    assert is_cache_fresh("AAPL", small_csv, data_dir=data_dir) is True

    # 缓存早于 csv → 过期
    os.utime(cache, (csv_mtime - 100, csv_mtime - 100))
    assert is_cache_fresh("AAPL", small_csv, data_dir=data_dir) is False


def test_load_or_compute_recomputes_when_csv_updated(
    small_csv: Path, tmp_path: Path
) -> None:
    """缓存存在但 csv 更新（mtime 更新）→ 重算并覆盖 parquet，反映新数据。"""
    data_dir = str(tmp_path / "data")
    first = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    orig_close = float(first["close"].iloc[-1])

    # 追加一行新数据并确保 mtime 更新
    new_row = pd.DataFrame(
        [["2024-01-16", 115.5, 117.0, 115.0, 116.5, 1_400_000]],
        columns=["date", "open", "high", "low", "close", "volume"],
    )
    pd.concat([pd.read_csv(small_csv), new_row]).to_csv(small_csv, index=False)
    os.utime(small_csv, None)  # 刷新 mtime 到当前

    second = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    assert len(second) == len(first) + 1
    assert float(second["close"].iloc[-1]) != orig_close


def test_clear_cache_removes_symbol_file(small_csv: Path, tmp_path: Path) -> None:
    """clear_cache(symbol) 删除该 symbol 的 parquet。"""
    data_dir = str(tmp_path / "data")
    load_or_compute("AAPL", small_csv, data_dir=data_dir)
    cache = cache_path("AAPL", data_dir)
    assert cache.exists()
    clear_cache("AAPL", data_dir=data_dir)
    assert not cache.exists()


def test_clear_cache_all_removes_everything(small_csv: Path, tmp_path: Path) -> None:
    """clear_cache(None) 删除缓存目录下全部 parquet。"""
    data_dir = str(tmp_path / "data")
    load_or_compute("AAPL", small_csv, data_dir=data_dir)
    cache_dir = Path(data_dir) / "cache"
    assert cache_path("AAPL", data_dir).exists()
    clear_cache(None, data_dir=data_dir)
    remaining = list(cache_dir.glob("*_indicators.parquet"))
    assert remaining == []


def test_load_or_compute_creates_cache_dir_when_absent(
    small_csv: Path, tmp_path: Path
) -> None:
    """缓存目录不存在时 load_or_compute 自动创建，不报错。"""
    data_dir = str(tmp_path / "fresh" / "data")
    assert not (Path(data_dir) / "cache").exists()
    df = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    assert "MA5" in df.columns
    assert cache_path("AAPL", data_dir).exists()


def test_load_or_compute_result_consumable_by_analyze(
    small_csv: Path, tmp_path: Path
) -> None:
    """缓存读出的 df 仍能被 analyze() 正常消费（端到端冒烟）。"""
    from src.analyze import analyze

    data_dir = str(tmp_path / "data")
    df = load_or_compute("AAPL", small_csv, data_dir=data_dir)
    result = analyze(df, "AAPL")
    assert isinstance(result, dict)
    assert "symbol" in result
