"""
技术指标计算模块 — 从本地 CSV 读取 OHLCV 数据并计算常用指标。

用法:
    from code.indicators import load_data, compute_all_indicators

    df = load_data("data/MSFT.csv")
    df = compute_all_indicators(df)
    print(df[["close", "MA5", "MA20", "RSI", "MACD"]].tail())
"""

import pandas as pd
import numpy as np


def load_data(path: str) -> pd.DataFrame:
    """读取 CSV 文件，返回标准化的 OHLCV DataFrame。"""
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").set_index("date")
    # 统一列名为小写
    df.columns = df.columns.str.lower()
    return df


# ── 均线 ────────────────────────────────────────────────────────────────

def add_ma(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """计算简单移动均线 (SMA)。默认 [5, 10, 20, 60, 120, 250]。"""
    if periods is None:
        periods = [5, 10, 20, 60, 120, 250]
    for p in periods:
        df[f"MA{p}"] = df["close"].rolling(p).mean()
    return df


def add_ema(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """计算指数移动均线 (EMA)。默认 [12, 26, 50, 144, 169]。"""
    if periods is None:
        periods = [12, 26, 50, 144, 169]
    for p in periods:
        df[f"EMA{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    return df


# ── RSI ─────────────────────────────────────────────────────────────────

def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """计算相对强弱指数 RSI。"""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder's smoothing (等效于 EMA 但使用 alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)  # 避免除零
    df["RSI"] = 100 - (100 / (1 + rs))
    # 修复: 价格完全不变时 (gain=loss=0) RSI 应为 50
    df["RSI"] = df["RSI"].fillna(50.0)
    return df


# ── MACD ────────────────────────────────────────────────────────────────

def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """计算 MACD、信号线、柱状图。"""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    return df


# ── 布林带 ──────────────────────────────────────────────────────────────

def add_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_multiplier: float = 2.0,
) -> pd.DataFrame:
    """计算布林带 (中轨/上轨/下轨)。"""
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    df["BB_mid"] = mid
    df["BB_upper"] = mid + std_multiplier * std
    df["BB_lower"] = mid - std_multiplier * std
    return df


# ── ATR (平均真实波幅) ──────────────────────────────────────────────────

def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """计算平均真实波幅 ATR。"""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / period, min_periods=period).mean()
    return df


# ── 成交量均线 ──────────────────────────────────────────────────────────

def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """成交量均线 + 量比。"""
    df["vol_MA20"] = df["volume"].rolling(period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_MA20"].replace(0, np.nan)
    return df


# ── 综合 ────────────────────────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """一键计算所有常用指标，原地修改并返回 DataFrame。"""
    add_ma(df)
    add_ema(df)
    add_rsi(df)
    add_macd(df)
    add_bollinger(df)
    add_atr(df)
    add_volume_ma(df)
    return df
