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


# ══════════════════════════════════════════════════════════════════════════
# Smart Money Concepts (SMC)
# ══════════════════════════════════════════════════════════════════════════


def add_smc_swing(df: pd.DataFrame, length: int = 5) -> pd.DataFrame:
    """
    检测摆动高低点 (Swing Highs / Lows)。
    生成列: SMC_swing_high, SMC_swing_low（非摆动点为 NaN）

    以 length 为半径的窗口内，最高/最低点即摆动点。
    注意：摆动点需要后续 length 根K线确认，最新 length 行无摆动点。
    """
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)

    for i in range(length, n - length):
        if highs[i] == highs[i - length : i + length + 1].max():
            swing_high[i] = highs[i]
        if lows[i] == lows[i - length : i + length + 1].min():
            swing_low[i] = lows[i]

    df["SMC_swing_high"] = swing_high
    df["SMC_swing_low"] = swing_low
    return df


def add_smc_fvg(df: pd.DataFrame) -> pd.DataFrame:
    """
    检测公允价值缺口 (Fair Value Gap)。
    生成列: SMC_FVG (1=bullish, -1=bearish, 0=无),
            SMC_FVG_top, SMC_FVG_bottom

    Bullish FVG: 当前K线低点 > 前两根K线高点（向上跳空缺口）
    Bearish FVG: 当前K线高点 < 前两根K线低点（向下跳空缺口）
    """
    high = df["high"]
    low = df["low"]

    bullish = low > high.shift(2)
    bearish = high < low.shift(2)

    df["SMC_FVG"] = 0
    df.loc[bullish, "SMC_FVG"] = 1
    df.loc[bearish, "SMC_FVG"] = -1

    # FVG 区间：bullish 时 bottom=前2根high, top=当前low
    df["SMC_FVG_top"] = np.where(bullish, low, np.where(bearish, low.shift(2), np.nan))
    df["SMC_FVG_bottom"] = np.where(
        bullish, high.shift(2), np.where(bearish, high, np.nan)
    )
    return df


def add_smc_structure(df: pd.DataFrame, swing_length: int = 5) -> pd.DataFrame:
    """
    计算市场结构：BOS (Break of Structure) 和 CHoCH (Change of Character)。
    生成列: SMC_BOS (1=bullish, -1=bearish),
            SMC_CHoCH (1=bullish, -1=bearish),
            SMC_structure (1=bullish trend, -1=bearish trend, 0=undefined)

    BOS: 趋势延续信号 — 上升趋势中突破前高，或下降趋势中突破前低
    CHoCH: 趋势反转信号 — 上升趋势中跌破前低，或下降趋势中突破前高
    """
    if "SMC_swing_high" not in df.columns:
        add_smc_swing(df, length=swing_length)

    n = len(df)
    closes = df["close"].values
    swing_highs = df["SMC_swing_high"].values
    swing_lows = df["SMC_swing_low"].values

    bos = np.zeros(n, dtype=int)
    choch = np.zeros(n, dtype=int)
    structure = np.zeros(n, dtype=int)

    last_sh = np.nan  # 最近的 swing high
    last_sl = np.nan  # 最近的 swing low
    trend = 0  # 0=undefined, 1=bullish, -1=bearish

    for i in range(n):
        # 更新摆动点（只在确认后更新，避免用未确认的点）
        if not np.isnan(swing_highs[i]):
            last_sh = swing_highs[i]
        if not np.isnan(swing_lows[i]):
            last_sl = swing_lows[i]

        if not np.isnan(last_sh) and closes[i] > last_sh:
            if trend == 1:
                bos[i] = 1  # 上升趋势中突破前高 → 延续
            elif trend == -1:
                choch[i] = 1  # 下降趋势中突破前高 → 反转
            else:
                bos[i] = 1  # 首次突破 → 定义为 BOS
            trend = 1
            last_sh = np.nan  # 重置被突破的摆动点
        elif not np.isnan(last_sl) and closes[i] < last_sl:
            if trend == -1:
                bos[i] = -1
            elif trend == 1:
                choch[i] = -1
            else:
                bos[i] = -1
            trend = -1
            last_sl = np.nan  # 重置被突破的摆动点

        structure[i] = trend

    df["SMC_BOS"] = bos
    df["SMC_CHoCH"] = choch
    df["SMC_structure"] = structure
    return df


def add_smc_order_blocks(
    df: pd.DataFrame, lookback: int = 10, swing_length: int = 5
) -> pd.DataFrame:
    """
    识别订单块 (Order Blocks)。
    生成列: SMC_OB (1=bullish, -1=bearish, 0=无),
            SMC_OB_top, SMC_OB_bottom

    在 BOS 信号后回溯，找到导致突破的最后一根反向K线：
    - Bullish OB: BOS 前的最后一根阴线 (close < open)
    - Bearish OB: BOS 前的最后一根阳线 (close > open)
    """
    if "SMC_BOS" not in df.columns:
        add_smc_structure(df, swing_length=swing_length)

    n = len(df)
    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    bos = df["SMC_BOS"].values

    ob = np.zeros(n, dtype=int)
    ob_top = np.full(n, np.nan)
    ob_bottom = np.full(n, np.nan)

    for i in range(n):
        if bos[i] == 0:
            continue
        direction = bos[i]  # 1=bullish BOS, -1=bearish BOS
        # 回溯找最后一根反向K线
        for j in range(i - 1, max(i - lookback - 1, -1), -1):
            if direction == 1 and closes[j] < opens[j]:
                # Bullish OB: 阴线，范围=该K线 low~high
                ob[j] = 1
                ob_top[j] = highs[j]
                ob_bottom[j] = lows[j]
                break
            elif direction == -1 and closes[j] > opens[j]:
                # Bearish OB: 阳线
                ob[j] = -1
                ob_top[j] = highs[j]
                ob_bottom[j] = lows[j]
                break

    df["SMC_OB"] = ob
    df["SMC_OB_top"] = ob_top
    df["SMC_OB_bottom"] = ob_bottom
    return df


def add_smc_premium_discount(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    """
    计算溢价/折价/均衡区 (Premium / Discount / Equilibrium Zones)。
    生成列: SMC_premium (75%线), SMC_equilibrium (50%线),
            SMC_discount (25%线), SMC_pd_zone ("premium"/"discount"/"equilibrium")

    基于最近 lookback 根K线的最高价/最低价计算摆动区间。
    """
    rolling_high = df["high"].rolling(lookback, min_periods=1).max()
    rolling_low = df["low"].rolling(lookback, min_periods=1).min()
    rng = rolling_high - rolling_low

    df["SMC_premium"] = rolling_low + rng * 0.75
    df["SMC_equilibrium"] = rolling_low + rng * 0.50
    df["SMC_discount"] = rolling_low + rng * 0.25

    close = df["close"]
    df["SMC_pd_zone"] = "equilibrium"
    df.loc[close > df["SMC_premium"], "SMC_pd_zone"] = "premium"
    df.loc[close < df["SMC_discount"], "SMC_pd_zone"] = "discount"
    return df


def add_smc_liquidity_sweep(df: pd.DataFrame, swing_length: int = 5) -> pd.DataFrame:
    """
    检测流动性扫荡 (Liquidity Sweep)。
    生成列: SMC_sweep (1=bullish, -1=bearish, 0=无),
            SMC_sweep_level (被扫荡的价格水平)

    Bullish sweep: 价格 low 跌破 swing low 但 close 回升到其上方
        → 下方止损被扫，聪明钱吸筹，可能反弹
    Bearish sweep: 价格 high 突破 swing high 但 close 回落到其下方
        → 上方止损被扫，聪明钱分发，可能回落
    """
    if "SMC_swing_high" not in df.columns:
        add_smc_swing(df, length=swing_length)

    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    swing_highs = df["SMC_swing_high"].values
    swing_lows = df["SMC_swing_low"].values

    sweep = np.zeros(n, dtype=int)
    sweep_level = np.full(n, np.nan)

    # 收集 swing points: (index, price)
    sh_points = [(i, swing_highs[i]) for i in range(n) if not np.isnan(swing_highs[i])]
    sl_points = [(i, swing_lows[i]) for i in range(n) if not np.isnan(swing_lows[i])]

    # Bearish sweep: wick 突破 swing high 但 close 回落
    for si, sp in sh_points:
        for j in range(si + swing_length + 1, n):
            if highs[j] > sp and closes[j] < sp:
                sweep[j] = -1
                sweep_level[j] = sp
                break  # 每个 swing point 只记一次

    # Bullish sweep: wick 跌破 swing low 但 close 回升
    for si, sp in sl_points:
        for j in range(si + swing_length + 1, n):
            if lows[j] < sp and closes[j] > sp:
                sweep[j] = 1
                sweep_level[j] = sp
                break

    df["SMC_sweep"] = sweep
    df["SMC_sweep_level"] = sweep_level
    return df


def add_smc_mtf(df: pd.DataFrame, swing_length: int = 3) -> pd.DataFrame:
    """
    多时间框架 SMC 分析（周线 → 日线映射）。
    生成列: SMC_weekly_structure (1=bullish, -1=bearish, 0=undefined),
            SMC_weekly_swing_high, SMC_weekly_swing_low,
            SMC_htf_bias ("strong_bullish"/"weak_bullish"/"strong_bearish"/
                          "weak_bearish"/"neutral")

    将日频数据 resample 到周频，计算周线结构方向，再映射回日频。
    日线与周线方向一致 → strong；不一致 → weak（可能回调/反弹）。
    """
    # Resample 到周频（周五结束）
    weekly = (
        df.resample("W-FRI")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    if len(weekly) < swing_length * 2 + 1:
        # 周线数据不足，填充默认值
        df["SMC_weekly_structure"] = 0
        df["SMC_weekly_swing_high"] = np.nan
        df["SMC_weekly_swing_low"] = np.nan
        df["SMC_htf_bias"] = "neutral"
        return df

    # 周频上计算 swing + structure
    add_smc_swing(weekly, length=swing_length)
    add_smc_structure(weekly, swing_length=swing_length)

    # 映射回日频（每根日K线继承所属周的结构）
    df["SMC_weekly_structure"] = (
        weekly["SMC_structure"].reindex(df.index, method="ffill").fillna(0).astype(int)
    )
    df["SMC_weekly_swing_high"] = weekly["SMC_swing_high"].reindex(
        df.index, method="ffill"
    )
    df["SMC_weekly_swing_low"] = weekly["SMC_swing_low"].reindex(
        df.index, method="ffill"
    )

    # 综合日线 + 周线方向
    daily = (
        df["SMC_structure"]
        if "SMC_structure" in df.columns
        else pd.Series(0, index=df.index)
    )
    wk = df["SMC_weekly_structure"]

    bias = pd.Series("neutral", index=df.index, dtype=str)
    bias[(daily == 1) & (wk == 1)] = "strong_bullish"
    bias[(daily == 1) & (wk == -1)] = "weak_bullish"
    bias[(daily == -1) & (wk == -1)] = "strong_bearish"
    bias[(daily == -1) & (wk == 1)] = "weak_bearish"
    df["SMC_htf_bias"] = bias

    return df


def add_smc(df: pd.DataFrame) -> pd.DataFrame:
    """一键计算所有 SMC 指标。"""
    add_smc_swing(df)
    add_smc_fvg(df)
    add_smc_structure(df)
    add_smc_order_blocks(df)
    add_smc_premium_discount(df)
    add_smc_liquidity_sweep(df)
    add_smc_mtf(df)
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
    add_smc(df)
    return df
