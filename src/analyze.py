#!/usr/bin/env python3
"""
K线技术分析脚本 — 读取本地 CSV，计算指标，输出诊断 JSON。

Rich 报告渲染已迁移至 TUI（技术分析模式侧栏）。CLI 仅保留 JSON 出口，
供 cron / 管道 / 外部工具消费。

用法:
    python -m src.analyze                            # 分析 data/MSFT.csv，输出 JSON
    python -m src.analyze data/AAPL.csv              # 指定文件
    python -m src.analyze data/AAPL.csv --as-of 2024-06-01  # 回看到指定日期
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

import pandas as pd

from src.indicators import compute_all_indicators, detect_cdl_hits, load_data

# 抑制 pandas 逐列 insert 的性能警告（不影响正确性）
warnings.filterwarnings("ignore", message=".*frame\\.insert.*")


def analyze(df: pd.DataFrame, symbol: str, as_of=None) -> dict:
    """从带指标的 DataFrame 提取诊断结果。

    as_of=None 时用最后一行（兼容现有 CLI）；传日期则截断到该日后取末行，
    实现“任意日期诊断”。截断后指标列天然反映截至那天的视角（无未来函数），
    无需额外处理。
    """
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        df = df.loc[:as_of]
    last = df.iloc[-1]
    cl = last["close"]

    # ── 均线 ──
    ma_signals = {}
    ma_values = {}
    for p in [5, 10, 20, 60, 120]:
        col = f"MA{p}"
        if col in df.columns and pd.notna(last.get(col)):
            ma_val = float(last[col])
            ma_signals[f"MA{p}"] = "above" if cl > ma_val else "below"
            ma_values[f"MA{p}"] = round(ma_val, 2)

    # ── RSI ──
    rsi = last.get("RSI")
    rsi = float(rsi) if pd.notna(rsi) else None

    # ── MACD ──
    macd = last.get("MACD")
    macd_signal = last.get("MACD_signal")
    macd_hist = last.get("MACD_hist")
    macd_status = (
        "golden_cross"
        if (pd.notna(macd) and pd.notna(macd_signal) and macd > macd_signal)
        else "dead_cross"
    )

    # ── 布林带 ──
    bb_upper = last.get("BB_upper")
    bb_lower = last.get("BB_lower")
    bb_mid = last.get("BB_mid")
    bb_pos = None
    if pd.notna(bb_upper) and pd.notna(bb_lower) and (bb_upper - bb_lower) > 0:
        bb_pos = (cl - bb_lower) / (bb_upper - bb_lower) * 100

    # ── ATR ──
    atr = last.get("ATR")
    atr = float(atr) if pd.notna(atr) else None

    # ── 成交量 ──
    vol_ratio = last.get("vol_ratio") if pd.notna(last.get("vol_ratio")) else None

    # ── ADX/DMI ──
    adx = last.get("ADX")
    adx = float(adx) if pd.notna(adx) else None
    dmp = last.get("DMP")
    dmp = float(dmp) if pd.notna(dmp) else None
    dmn = last.get("DMN")
    dmn = float(dmn) if pd.notna(dmn) else None
    adx_trend = None
    if adx is not None:
        if adx > 25:
            adx_trend = "trending" if dmp is not None and dmn is not None else "strong"
        elif adx < 20:
            adx_trend = "ranging"
        else:
            adx_trend = "weak_trend"

    # ── Stochastic ──
    stoch_k = last.get("STOCH_k")
    stoch_k = float(stoch_k) if pd.notna(stoch_k) else None
    stoch_d = last.get("STOCH_d")
    stoch_d = float(stoch_d) if pd.notna(stoch_d) else None
    stoch_detail = None
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20 and stoch_d < 20:
            stoch_detail = "oversold"
        elif stoch_k > 80 and stoch_d > 80:
            stoch_detail = "overbought"
        else:
            stoch_detail = "neutral"

    # ── SuperTrend ──
    supert_dir = last.get("SUPERT_dir")
    supert_dir = int(supert_dir) if pd.notna(supert_dir) else None
    supert_long = last.get("SUPERT_long_stop")
    supert_long = float(supert_long) if pd.notna(supert_long) else None
    supert_short = last.get("SUPERT_short_stop")
    supert_short = float(supert_short) if pd.notna(supert_short) else None

    # ── OBV 背离检测 ──
    obv = last.get("OBV")
    obv = float(obv) if pd.notna(obv) else None
    obv_divergence = None
    if obv is not None and len(df) >= 20:
        # 20日价格高点 vs OBV 高点
        recent_20 = df.tail(20)
        price_20h = recent_20["close"].max()
        idx_price = recent_20["close"].idxmax()
        idx_obv = recent_20["OBV"].idxmax()
        if (
            idx_price > idx_obv
            and price_20h > recent_20["close"].iloc[-10:].max() * 0.99
        ):
            obv_divergence = "bearish_divergence"
        # 20日价格低点 vs OBV 低点
        price_20l = recent_20["close"].min()
        idx_price_l = recent_20["close"].idxmin()
        idx_obv_l = recent_20["OBV"].idxmin()
        if (
            idx_price_l > idx_obv_l
            and price_20l < recent_20["close"].iloc[-10:].min() * 1.01
        ):
            obv_divergence = "bullish_divergence"

    # ── CCI ──
    cci = last.get("CCI")
    cci = float(cci) if pd.notna(cci) else None
    cci_detail = None
    if cci is not None:
        if cci > 200:
            cci_detail = "extreme_overbought"
        elif cci > 100:
            cci_detail = "overbought"
        elif cci < -200:
            cci_detail = "extreme_oversold"
        elif cci < -100:
            cci_detail = "oversold"
        else:
            cci_detail = "normal"

    # ── MFI ──
    mfi = last.get("MFI")
    mfi = float(mfi) if pd.notna(mfi) else None

    # ── 蜡烛形态（按 as_of 查命中，避免全量 attrs 的未来污染） ──
    cdl_bullish, cdl_bearish = detect_cdl_hits(df, as_of)

    # ── SMC (Smart Money Concepts) ──
    smc_structure = last.get("SMC_structure")
    smc_structure = int(smc_structure) if pd.notna(smc_structure) else None
    smc_pd_zone = last.get("SMC_pd_zone", None)
    smc_premium = last.get("SMC_premium")
    smc_premium = float(smc_premium) if pd.notna(smc_premium) else None
    smc_equilibrium = last.get("SMC_equilibrium")
    smc_equilibrium = float(smc_equilibrium) if pd.notna(smc_equilibrium) else None
    smc_discount = last.get("SMC_discount")
    smc_discount = float(smc_discount) if pd.notna(smc_discount) else None

    # 最近 5 天内的 BOS/CHoCH 信号
    recent_5 = df.tail(5)
    smc_recent_bos = int(recent_5["SMC_BOS"].sum()) if "SMC_BOS" in df.columns else 0
    smc_recent_choch = (
        int(recent_5["SMC_CHoCH"].sum()) if "SMC_CHoCH" in df.columns else 0
    )

    # 最近的 FVG（最近 10 天）
    recent_10 = df.tail(10)
    smc_recent_fvg = (
        int(recent_10["SMC_FVG"].abs().sum()) if "SMC_FVG" in df.columns else 0
    )

    # 最近的 Order Block（最近 10 天）
    smc_recent_ob = (
        int(recent_10["SMC_OB"].abs().sum()) if "SMC_OB" in df.columns else 0
    )

    # Liquidity Sweep
    smc_sweep = last.get("SMC_sweep")
    smc_sweep = int(smc_sweep) if pd.notna(smc_sweep) else None
    smc_sweep_level = last.get("SMC_sweep_level")
    smc_sweep_level = float(smc_sweep_level) if pd.notna(smc_sweep_level) else None
    smc_recent_sweep = (
        int(recent_10["SMC_sweep"].abs().sum()) if "SMC_sweep" in df.columns else 0
    )

    # MTF (多时间框架)
    smc_weekly_structure = last.get("SMC_weekly_structure")
    smc_weekly_structure = (
        int(smc_weekly_structure) if pd.notna(smc_weekly_structure) else None
    )
    smc_htf_bias = last.get("SMC_htf_bias", None)

    # ── 近期涨跌 ──
    changes = {}
    for days, label in [(5, "5d"), (21, "1m"), (63, "3m"), (126, "6m"), (252, "1y")]:
        if len(df) >= days:
            chg = (cl - df["close"].iloc[-days]) / df["close"].iloc[-days] * 100
            changes[label] = round(chg, 2)

    # ── 关键价位 (90日) ──
    recent = df.tail(90)
    resistance = float(recent["high"].max())
    support = float(recent["low"].min())

    # ── 综合评分 ──
    scores = []
    ma5 = last.get("MA5")
    ma20 = last.get("MA20")
    ma60 = last.get("MA60")
    if pd.notna(ma5) and pd.notna(ma20) and pd.notna(ma60):
        if ma5 > ma20 > ma60:
            scores.append(("MA多头排列", 2))
        elif ma5 < ma20 < ma60:
            scores.append(("MA空头排列", -2))
        else:
            scores.append(("MA交织", 0))
    if pd.notna(ma60):
        scores.append(
            ("价格站上MA60" if cl > ma60 else "价格跌破MA60", 1 if cl > ma60 else -1)
        )

    # RSI 评分
    if rsi is not None:
        if rsi < 30:
            scores.append(("RSI超卖", 1))
        elif rsi > 70:
            scores.append(("RSI超买", -1))
        elif rsi < 40:
            scores.append(("RSI偏弱", 0))
        elif rsi > 60:
            scores.append(("RSI偏强", 0))
        else:
            scores.append(("RSI中性", 0))

    # MACD 评分
    if pd.notna(macd) and pd.notna(macd_signal) and pd.notna(macd_hist):
        if macd > macd_signal:
            scores.append(("MACD金叉", 1))
        else:
            scores.append(("MACD死叉", -1))
        prev_hist = df["MACD_hist"].iloc[-2] if len(df) >= 2 else None
        if prev_hist is not None and pd.notna(prev_hist):
            if abs(macd_hist) > abs(prev_hist):
                scores.append(("MACD动能放大", 1 if macd_hist > 0 else -1))
            else:
                scores.append(("MACD动能收缩", 0))

    # ADX/DMI 评分
    if adx is not None and dmp is not None and dmn is not None:
        if adx > 25:
            scores.append(("ADX趋势市", 1 if dmp > dmn else -1))
        elif adx < 20:
            scores.append(("ADX震荡市", -1))

    # Stochastic 评分
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20:
            scores.append(("Stoch超卖", 1))
        elif stoch_k > 80:
            scores.append(("Stoch超买", -1))
        if stoch_k > stoch_d:
            scores.append(("Stoch金叉(%K>%D)", 1))
        else:
            scores.append(("Stoch死叉(%K<%D)", -1))

    # SuperTrend 评分
    if supert_dir is not None:
        scores.append(
            (
                "SuperTrend多头" if supert_dir == 1 else "SuperTrend空头",
                1 if supert_dir == 1 else -1,
            )
        )

    # OBV 背离评分
    if obv_divergence == "bullish_divergence":
        scores.append(("OBV底背离", 2))
    elif obv_divergence == "bearish_divergence":
        scores.append(("OBV顶背离", -2))

    # CCI 评分
    if cci is not None:
        if cci < -200:
            scores.append(("CCI极度超卖", 2))
        elif cci < -100:
            scores.append(("CCI超卖", 1))
        elif cci > 200:
            scores.append(("CCI极度超买", -2))
        elif cci > 100:
            scores.append(("CCI超买", -1))

    # MFI 评分
    if mfi is not None:
        if mfi < 20:
            scores.append(("MFI超卖", 1))
        elif mfi > 80:
            scores.append(("MFI超买", -1))
        # MFI vs RSI 背离检测（反证信号）
        if rsi is not None:
            if rsi < 30 and mfi > rsi + 15:
                scores.append(("量价背离(RSI超卖)", -1))
            elif rsi > 70 and mfi < rsi - 15:
                scores.append(("量价背离(RSI超买)", 1))

    # 蜡烛形态评分
    if cdl_bullish:
        for name in cdl_bullish:
            scores.append((f"K线{name}(看多)", 1))
    if cdl_bearish:
        for name in cdl_bearish:
            scores.append((f"K线{name}(看空)", -1))

    # SMC 评分
    if smc_structure is not None:
        if smc_structure == 1:
            scores.append(("SMC结构多头", 1))
        elif smc_structure == -1:
            scores.append(("SMC结构空头", -1))
    if smc_recent_bos > 0:
        scores.append(("SMC近期BOS(多)", 1))
    elif smc_recent_bos < 0:
        scores.append(("SMC近期BOS(空)", -1))
    if smc_recent_choch > 0:
        scores.append(("SMC近期CHoCH(反转多)", 2))
    elif smc_recent_choch < 0:
        scores.append(("SMC近期CHoCH(反转空)", -2))
    if smc_pd_zone == "premium":
        scores.append(("SMC溢价区", -1))
    elif smc_pd_zone == "discount":
        scores.append(("SMC折价区", 1))

    # Liquidity Sweep 评分
    if smc_recent_sweep > 0:
        # 最近的 sweep 方向
        last_sweeps = df[df["SMC_sweep"] != 0].tail(3)
        if not last_sweeps.empty:
            latest = int(last_sweeps["SMC_sweep"].iloc[-1])
            if latest == 1:
                scores.append(("SMC流动性扫荡(下方吸筹)", 1))
            else:
                scores.append(("SMC流动性扫荡(上方分发)", -1))

    # MTF 评分
    if smc_htf_bias == "strong_bullish":
        scores.append(("SMC多时间框架强多", 2))
    elif smc_htf_bias == "strong_bearish":
        scores.append(("SMC多时间框架强空", -2))
    elif smc_htf_bias == "weak_bullish":
        scores.append(("SMC多时间框架弱多(周线回调)", 0))
    elif smc_htf_bias == "weak_bearish":
        scores.append(("SMC多时间框架弱空(周线反弹)", 0))

    total = sum(s for _, s in scores)
    rsi_detail = (
        "oversold"
        if rsi and rsi < 30
        else ("overbought" if rsi and rsi > 70 else "neutral")
    )

    return {
        "symbol": symbol,
        "last_price": round(cl, 2),
        "last_date": str(df.index[-1].date()),
        "total_days": len(df),
        "ma_signals": ma_signals,
        "ma_values": ma_values,
        "RSI": round(rsi, 1) if rsi else None,
        "RSI_detail": rsi_detail,
        "MACD": round(float(macd), 3) if pd.notna(macd) else None,
        "MACD_signal": round(float(macd_signal), 3) if pd.notna(macd_signal) else None,
        "MACD_hist": round(float(macd_hist), 3) if pd.notna(macd_hist) else None,
        "MACD_status": macd_status,
        "BB_upper": round(float(bb_upper), 2) if pd.notna(bb_upper) else None,
        "BB_lower": round(float(bb_lower), 2) if pd.notna(bb_lower) else None,
        "BB_mid": round(float(bb_mid), 2) if pd.notna(bb_mid) else None,
        "BB_position": round(bb_pos, 0) if bb_pos is not None else None,
        "ATR": round(atr, 2) if atr else None,
        "vol_ratio": round(float(vol_ratio), 2) if vol_ratio else None,
        # 新增指标
        "ADX": round(adx, 1) if adx else None,
        "DMP": round(dmp, 1) if dmp else None,
        "DMN": round(dmn, 1) if dmn else None,
        "ADX_trend": adx_trend,
        "STOCH_k": round(stoch_k, 1) if stoch_k else None,
        "STOCH_d": round(stoch_d, 1) if stoch_d else None,
        "STOCH_detail": stoch_detail,
        "SUPERT_dir": supert_dir,
        "SUPERT_long_stop": round(supert_long, 2) if supert_long else None,
        "SUPERT_short_stop": round(supert_short, 2) if supert_short else None,
        "OBV_divergence": obv_divergence,
        "CCI": round(cci, 1) if cci else None,
        "CCI_detail": cci_detail,
        "MFI": round(mfi, 1) if mfi else None,
        # SMC
        "SMC_structure": smc_structure,
        "SMC_pd_zone": smc_pd_zone,
        "SMC_premium": round(smc_premium, 2) if smc_premium else None,
        "SMC_equilibrium": round(smc_equilibrium, 2) if smc_equilibrium else None,
        "SMC_discount": round(smc_discount, 2) if smc_discount else None,
        "SMC_recent_BOS": smc_recent_bos,
        "SMC_recent_CHoCH": smc_recent_choch,
        "SMC_recent_FVG": smc_recent_fvg,
        "SMC_recent_OB": smc_recent_ob,
        "SMC_sweep": smc_sweep,
        "SMC_sweep_level": round(smc_sweep_level, 2) if smc_sweep_level else None,
        "SMC_recent_sweep": smc_recent_sweep,
        "SMC_weekly_structure": smc_weekly_structure,
        "SMC_htf_bias": smc_htf_bias,
        "cdl_bullish": cdl_bullish,
        "cdl_bearish": cdl_bearish,
        # 原有
        "changes": changes,
        "resistance_90d": resistance,
        "support_90d": support,
        "scores": [{"label": lb, "value": v} for lb, v in scores],
        "total_score": total,
    }


def main():
    parser = argparse.ArgumentParser(description="K线技术分析（输出 JSON）")
    parser.add_argument("csv", nargs="?", default="data/MSFT.csv", help="CSV 文件路径")
    parser.add_argument(
        "--as-of",
        default=None,
        help="回看到指定日期（YYYY-MM-DD），默认用最后一行",
    )
    # --json 保留兼容（默认即 JSON），无实际分支作用
    parser.add_argument(
        "--json", action="store_true", help="输出 JSON（默认行为，保留兼容）"
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"错误: 文件不存在 {csv_path}", file=sys.stderr)
        sys.exit(1)

    symbol = csv_path.stem.upper()
    df = load_data(str(csv_path))
    df = compute_all_indicators(df)
    result = analyze(df, symbol, as_of=args.as_of)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
