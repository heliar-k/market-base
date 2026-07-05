"""
技术指标计算模块 — 从本地 CSV 读取 OHLCV 数据并计算常用指标。

用法:
    from src.indicators import load_data, compute_all_indicators

    df = load_data("data/MSFT.csv")
    df = compute_all_indicators(df)
    print(df[["close", "MA5", "MA20", "RSI", "MACD"]].tail())
"""

import numpy as np
import pandas as pd
import pandas_ta_classic as ta


def load_data(path: str) -> pd.DataFrame:
    """读取 CSV 文件，返回标准化的 OHLCV DataFrame。"""
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").set_index("date")
    # 统一列名为小写
    df.columns = df.columns.str.lower()
    return df


# ── 均线 ────────────────────────────────────────────────────────────────


def _merge_ta_columns(df: pd.DataFrame, result, aliases: dict[str, str]) -> None:
    """将 pandas-ta 结果列合并到 df，并按 aliases 创建短别名列。"""
    if result is None:
        return
    for col in result.columns:
        df[col] = result[col]
    for alias, source in aliases.items():
        df[alias] = result[source]


def add_ma(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """计算简单移动均线 (SMA)。默认 [5, 10, 20, 60, 120]。"""
    if periods is None:
        periods = [5, 10, 20, 60, 120]
    for p in periods:
        df[f"MA{p}"] = ta.sma(df["close"], length=p)
    return df


# ── RSI ─────────────────────────────────────────────────────────────────


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """计算相对强弱指数 RSI（Wilder's smoothing）。"""
    df["RSI"] = ta.rsi(df["close"], length=period)
    return df


# ── MACD ────────────────────────────────────────────────────────────────


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """计算 MACD、信号线、柱状图。"""
    _merge_ta_columns(
        df,
        ta.macd(df["close"], fast=fast, slow=slow, signal=signal),
        {
            "MACD": f"MACD_{fast}_{slow}_{signal}",
            "MACD_hist": f"MACDh_{fast}_{slow}_{signal}",
            "MACD_signal": f"MACDs_{fast}_{slow}_{signal}",
        },
    )
    return df


# ── 布林带 ──────────────────────────────────────────────────────────────


def add_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_multiplier: float = 2.0,
) -> pd.DataFrame:
    """计算布林带 (中轨/上轨/下轨)。"""
    mstr = f"{std_multiplier:.1f}"
    _merge_ta_columns(
        df,
        ta.bbands(df["close"], length=period, std=std_multiplier),
        {
            "BB_lower": f"BBL_{period}_{mstr}",
            "BB_mid": f"BBM_{period}_{mstr}",
            "BB_upper": f"BBU_{period}_{mstr}",
        },
    )
    return df


# ── ATR (平均真实波幅) ──────────────────────────────────────────────────


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """计算平均真实波幅 ATR（Wilder's smoothing）。"""
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=period)
    return df


# ── 成交量均线 ──────────────────────────────────────────────────────────


def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """成交量均线 + 量比。"""
    df[f"vol_MA{period}"] = df["volume"].rolling(period).mean()
    df["vol_ratio"] = df["volume"] / df[f"vol_MA{period}"].replace(0, np.nan)
    return df


# ══════════════════════════════════════════════════════════════════════════
# 扩展指标 (基于 pandas-ta-classic)
# ══════════════════════════════════════════════════════════════════════════


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
    _merge_ta_columns(
        df,
        ta.adx(df["high"], df["low"], df["close"], length=period, drift=drift),
        {
            "ADX": f"ADX_{period}",
            "DMP": f"DMP_{period}",
            "DMN": f"DMN_{period}",
        },
    )
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
    _merge_ta_columns(
        df,
        ta.stoch(
            df["high"], df["low"], df["close"], k=k_period, d=d_period, smooth_k=smooth
        ),
        {
            "STOCH_k": f"STOCHk_{k_period}_{d_period}_{smooth}",
            "STOCH_d": f"STOCHd_{k_period}_{d_period}_{smooth}",
        },
    )
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
    mstr = f"{multiplier:.1f}"
    _merge_ta_columns(
        df,
        ta.supertrend(
            df["high"], df["low"], df["close"], length=period, multiplier=multiplier
        ),
        {
            "SUPERT": f"SUPERT_{period}_{mstr}",
            "SUPERT_dir": f"SUPERTd_{period}_{mstr}",
            "SUPERT_long_stop": f"SUPERTl_{period}_{mstr}",
            "SUPERT_short_stop": f"SUPERTs_{period}_{mstr}",
        },
    )
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


def detect_cdl_hits(df: pd.DataFrame, as_of=None) -> tuple[list[str], list[str]]:
    """检测指定行命中的反转蜡烛形态，返回 (看多形态名, 看空形态名)。

    as_of=None → 用最后一行；as_of 为日期则查该行的 CDL_* 列。
    CDL 列无未来函数（每行仅依赖该日及之前 K 线），故直接按行查询即可，
    无需截断 df。
    """
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        row = df.loc[as_of]
    else:
        row = df.iloc[-1]
    bullish_hits = [
        CDL_BULLISH[col]
        for col in CDL_BULLISH
        if col in df.columns and row.get(col, 0) != 0
    ]
    bearish_hits = [
        CDL_BEARISH[col]
        for col in CDL_BEARISH
        if col in df.columns and row.get(col, 0) != 0
    ]
    return bullish_hits, bearish_hits


def add_cdl_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    识别 62 种 K 线形态（pandas-ta 原生，无需 TA-Lib）。
    生成列: CDL_* (62 列)，并在 df.attrs 存储最新行命中的反转形态。
    """
    result = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name="all")
    if result is not None:
        # 一次性合并 CDL 列，避免逐列 insert 导致碎片化（PerformanceWarning）
        saved_attrs = df.attrs
        df = pd.concat([df, result], axis=1)
        df.attrs = saved_attrs

        # 最新行命中存入 attrs（保持原行为）
        bullish_hits, bearish_hits = detect_cdl_hits(df)
        df.attrs["cdl_bullish"] = bullish_hits
        df.attrs["cdl_bearish"] = bearish_hits
    return df


# ── 综合 ────────────────────────────────────────────────────────────────


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    一键计算所有常用指标，原地修改并返回 DataFrame。
    """
    add_ma(df)
    add_rsi(df)
    add_macd(df)
    add_bollinger(df)
    add_atr(df)
    add_volume_ma(df)
    add_adx(df)
    add_stochastic(df)
    add_supertrend(df)
    add_obv(df)
    add_cci(df)
    add_mfi(df)
    df = add_cdl_patterns(df)
    return df
