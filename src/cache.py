"""TUI 指标缓存层。

缓存 compute_all_indicators(df) 的结果到 parquet，TUI 启动/切标的时直接读盘，
避免对 ~2500 行 df 重算 14 类指标 + 62 列 CDL 形态的可感知延迟。

缓存什么：带全部指标列的 df（等价 compute_all_indicators(load_data(csv))）。
失效策略：缓存文件 mtime < 源 CSV mtime → 重算。一行 path.stat().st_mtime。
不存 attrs：parquet 不持久化 df.attrs，但 analyze() 现在用 detect_cdl_hits(df, as_of)
按行查询，不读 attrs，故无需重算 attrs，保持简单。
"""

from pathlib import Path

import pandas as pd

from src.indicators import compute_all_indicators, load_data


def cache_path(symbol: str, data_dir: str = "data") -> Path:
    """返回缓存文件路径 data/cache/{SYMBOL}_indicators.parquet。"""
    return Path(data_dir) / "cache" / f"{symbol}_indicators.parquet"


def is_cache_fresh(symbol: str, csv_path: Path, data_dir: str = "data") -> bool:
    """缓存是否新鲜：缓存文件存在 且 mtime >= csv_path mtime。"""
    cache = cache_path(symbol, data_dir)
    if not cache.exists():
        return False
    return cache.stat().st_mtime >= csv_path.stat().st_mtime


def load_or_compute(
    symbol: str, csv_path: Path, data_dir: str = "data"
) -> pd.DataFrame:
    """加载带指标的 df：缓存新鲜则读 parquet，否则读 CSV + 算指标 + 写 parquet。"""
    cache = cache_path(symbol, data_dir)
    if is_cache_fresh(symbol, csv_path, data_dir):
        return pd.read_parquet(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df = compute_all_indicators(load_data(str(csv_path)))
    df.to_parquet(cache)
    return df


def clear_cache(symbol: str | None = None, data_dir: str = "data") -> None:
    """清除缓存：symbol=None 清全部，否则清指定 symbol。"""
    cache_dir = Path(data_dir) / "cache"
    if symbol is not None:
        cache_path(symbol, data_dir).unlink(missing_ok=True)
        return
    for p in cache_dir.glob("*_indicators.parquet"):
        p.unlink(missing_ok=True)
