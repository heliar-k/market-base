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


# ══════════════════════════════════════════════════════════════════════════
# 新增指标 (基于 pandas-ta-classic)
# ══════════════════════════════════════════════════════════════════════════

import pandas_ta_classic as ta


def add_adx(
    df: pd.DataFrame,
    period: int = 14,
    drift: int = 1,
) -> pd.DataFrame:
    """
    计算 ADX/DMI 趋势强度指标。
    生成列: ADX, DMP (DI+), DMN (DI-)
    - ADX > 25: 趋势市; ADX < 20: 震荡市
    - DMP > DMN: 多头占优; DMN > DMP: 空头占优
    """
    result = ta.adx(df["high"], df["low"], df["close"], length=period, drift=drift)
    if result is not None:
        for col in result.columns:
            df[col] = result[col]
        # 短别名便于引用
        df["ADX"] = result[f"ADX_{period}"]
        df["DMP"] = result[f"DMP_{period}"]
        df["DMN"] = result[f"DMN_{period}"]
    return df


def add_stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> pd.DataFrame:
    """
    计算慢速随机指标 (Stochastic)。
    生成列: STOCH_k (%K), STOCH_d (%D)
    - %K/%D < 20: 超卖; %K/%D > 80: 超买
    - %K 上穿 %D: 金叉; %K 下穿 %D: 死叉
    """
    result = ta.stoch(df["high"], df["low"], df["close"], k=k_period, d=d_period, smooth_k=smooth)
    if result is not None:
        for col in result.columns:
            df[col] = result[col]
        df["STOCH_k"] = result[f"STOCHk_{k_period}_{d_period}_{smooth}"]
        df["STOCH_d"] = result[f"STOCHd_{k_period}_{d_period}_{smooth}"]
    return df


def add_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """
    计算 SuperTrend 指标。
    生成列:
    - SUPERT: 趋势值
    - SUPERT_dir: 方向 (1=多头, -1=空头)
    - SUPERT_long_stop: 多头止损线（牛市有效，价格下方）
    - SUPERT_short_stop: 空头止损线（熊市有效，价格上方）
    """
    result = ta.supertrend(df["high"], df["low"], df["close"], length=period, multiplier=multiplier)
    if result is not None:
        mstr = f"{multiplier:.1f}"
        for col in result.columns:
            df[col] = result[col]
        df["SUPERT"] = result[f"SUPERT_{period}_{mstr}"]
        df["SUPERT_dir"] = result[f"SUPERTd_{period}_{mstr}"]
        df["SUPERT_long_stop"] = result[f"SUPERTl_{period}_{mstr}"]   # 多头止损/支撑
        df["SUPERT_short_stop"] = result[f"SUPERTs_{period}_{mstr}"]  # 空头止损/阻力
    return df


def add_obv(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算能量潮指标 (On-Balance Volume)。
    生成列: OBV
    - 价格新高但 OBV 不跟 → 量价背离 → 反转预警
    """
    result = ta.obv(df["close"], df["volume"])
    if result is not None:
        df["OBV"] = result
    return df


def add_cci(
    df: pd.DataFrame,
    period: int = 20,
    constant: float = 0.015,
) -> pd.DataFrame:
    """
    计算商品通道指数 (CCI)。
    生成列: CCI
    - CCI > +100: 异常强势; CCI < -100: 异常弱势
    - CCI 返回 ±100 区间: 回归正常
    """
    result = ta.cci(df["high"], df["low"], df["close"], length=period, c=constant)
    if result is not None:
        df["CCI"] = result
    return df


def add_mfi(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.DataFrame:
    """
    计算资金流量指数 (MFI)，成交量加权的 RSI。
    生成列: MFI
    - MFI > 80: 超买（资金过度流入），MFI < 20: 超卖（资金过度流出）
    - 与 RSI 方向不一致 → 量价背离 → 天然反证信号
    """
    result = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=period)
    if result is not None:
        df["MFI"] = result
    return df


# ── 蜡烛形态 ────────────────────────────────────────────────────────────

# 关键反转形态
CDL_BULLISH = {
    "CDL_HAMMER": "锤子线",
    "CDL_INVERTEDHAMMER": "倒锤子",
    "CDL_ENGULFING": "看涨吞没",
    "CDL_MORNINGSTAR": "晨星",
    "CDL_PIERCING": "刺透形态",
    "CDL_DOJI": "十字星",
}
CDL_BEARISH = {
    "CDL_SHOOTINGSTAR": "射击之星",
    "CDL_HANGINGMAN": "吊颈线",
    "CDL_EVENINGSTAR": "黄昏之星",
    "CDL_DARKCLOUDCOVER": "乌云盖顶",
    "CDL_BELTHOLD": "捉腰带线",
}


def add_cdl_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    识别 62 种 K 线形态（pandas-ta 原生，无需 TA-Lib）。
    生成列: CDL_* (62 列)，并在 df.attrs 存储最新行命中的反转形态。
    """
    result = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name="all")
    if result is not None:
        for col in result.columns:
            df[col] = result[col]

        # 提取最新行命中的反转形态
        last = df.iloc[-1]
        bullish_hits = [CDL_BULLISH[col] for col in CDL_BULLISH
                        if col in df.columns and last.get(col, 0) != 0]
        bearish_hits = [CDL_BEARISH[col] for col in CDL_BEARISH
                        if col in df.columns and last.get(col, 0) != 0]
        df.attrs["cdl_bullish"] = bullish_hits
        df.attrs["cdl_bearish"] = bearish_hits
    return df


# ── 综合 ────────────────────────────────────────────────────────────────

def compute_all_indicators(
    df: pd.DataFrame,
    extended: bool = True,
) -> pd.DataFrame:
    """
    一键计算所有常用指标，原地修改并返回 DataFrame。

    参数:
        extended: 是否启用 pandas-ta 扩展指标
    """
    add_ma(df)
    add_ema(df)
    add_rsi(df)
    add_macd(df)
    add_bollinger(df)
    add_atr(df)
    add_volume_ma(df)

    if extended:
        add_adx(df)
        add_stochastic(df)
        add_supertrend(df)
        add_obv(df)
        add_cci(df)
        add_mfi(df)
        add_cdl_patterns(df)

    return df
