#!/usr/bin/env python3
"""
K线技术分析脚本 — 读取本地 CSV 并输出完整技术分析报告。

用法:
    python code/analyze.py                          # 分析 data/MSFT.csv
    python code/analyze.py data/AAPL.csv            # 分析指定文件
    python code/analyze.py data/MSFT.csv --no-print # 仅输出最新行 JSON
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

# 允许从项目根目录 import code.indicators
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code.indicators import load_data, compute_all_indicators


def analyze(df: pd.DataFrame, symbol: str) -> dict:
    """从带指标的 DataFrame 提取诊断结果。"""
    last = df.iloc[-1]
    cl = last["close"]

    # ── 均线 ──
    ma_signals = {}
    for p in [5, 10, 20, 60, 120]:
        col = f"MA{p}"
        if col in df.columns and pd.notna(last.get(col)):
            ma_signals[f"MA{p}"] = "above" if cl > last[col] else "below"

    # ── RSI ──
    rsi = last.get("RSI")
    rsi = float(rsi) if pd.notna(rsi) else None

    # ── MACD ──
    macd = last.get("MACD")
    macd_signal = last.get("MACD_signal")
    macd_hist = last.get("MACD_hist")
    macd_status = "golden_cross" if (pd.notna(macd) and pd.notna(macd_signal) and macd > macd_signal) else "dead_cross"

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
        obv_20h = recent_20["OBV"].max()
        idx_price = recent_20["close"].idxmax()
        idx_obv = recent_20["OBV"].idxmax()
        if idx_price > idx_obv and price_20h > recent_20["close"].iloc[-10:].max() * 0.99:
            obv_divergence = "bearish_divergence"
        # 20日价格低点 vs OBV 低点
        price_20l = recent_20["close"].min()
        obv_20l = recent_20["OBV"].min()
        idx_price_l = recent_20["close"].idxmin()
        idx_obv_l = recent_20["OBV"].idxmin()
        if idx_price_l > idx_obv_l and price_20l < recent_20["close"].iloc[-10:].min() * 1.01:
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

    # ── 蜡烛形态 ──
    cdl_bullish = df.attrs.get("cdl_bullish", [])
    cdl_bearish = df.attrs.get("cdl_bearish", [])

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
            scores.append(("MA_bullish", 2))
        elif ma5 < ma20 < ma60:
            scores.append(("MA_bearish", -2))
        else:
            scores.append(("MA_mixed", 0))
    if pd.notna(ma60):
        scores.append(("price_vs_MA60_bull" if cl > ma60 else "price_vs_MA60_bear", 1 if cl > ma60 else -1))

    # RSI 评分
    if rsi is not None:
        if rsi < 30:
            scores.append(("RSI_oversold(反弹机会)", 1))
        elif rsi > 70:
            scores.append(("RSI_overbought(回调风险)", -1))
        elif rsi < 40:
            scores.append(("RSI_偏弱", 0))
        elif rsi > 60:
            scores.append(("RSI_偏强", 0))
        else:
            scores.append(("RSI_neutral", 0))

    # MACD 评分
    if pd.notna(macd) and pd.notna(macd_signal) and pd.notna(macd_hist):
        if macd > macd_signal:
            scores.append(("MACD_金叉", 1))
        else:
            scores.append(("MACD_死叉", -1))
        prev_hist = df["MACD_hist"].iloc[-2] if len(df) >= 2 else None
        if prev_hist is not None and pd.notna(prev_hist):
            if abs(macd_hist) > abs(prev_hist):
                scores.append(("MACD_hist_expanding", 1 if macd_hist > 0 else -1))
            else:
                scores.append(("MACD_hist_contracting", 0))

    # ADX/DMI 评分
    if adx is not None and dmp is not None and dmn is not None:
        if adx > 25:
            scores.append(("ADX_趋势市", 1 if dmp > dmn else -1))
        elif adx < 20:
            scores.append(("ADX_震荡市(均线类信号可靠性低)", -1))

    # Stochastic 评分
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20:
            scores.append(("Stoch_超卖", 1))
        elif stoch_k > 80:
            scores.append(("Stoch_超买", -1))
        if stoch_k > stoch_d:
            scores.append(("Stoch_%K>%D", 1))
        else:
            scores.append(("Stoch_%K<%D", -1))

    # SuperTrend 评分
    if supert_dir is not None:
        scores.append(("SuperTrend_多头" if supert_dir == 1 else "SuperTrend_空头", 1 if supert_dir == 1 else -1))

    # OBV 背离评分
    if obv_divergence == "bullish_divergence":
        scores.append(("OBV_底背离(反转预警)", 2))
    elif obv_divergence == "bearish_divergence":
        scores.append(("OBV_顶背离(反转预警)", -2))

    # CCI 评分
    if cci is not None:
        if cci < -200:
            scores.append(("CCI_极度超卖(反转机会)", 2))
        elif cci < -100:
            scores.append(("CCI_超卖", 1))
        elif cci > 200:
            scores.append(("CCI_极度超买(反转风险)", -2))
        elif cci > 100:
            scores.append(("CCI_超买", -1))

    # MFI 评分
    if mfi is not None:
        if mfi < 20:
            scores.append(("MFI_超卖(资金流出衰竭)", 1))
        elif mfi > 80:
            scores.append(("MFI_超买(资金流入衰竭)", -1))
        # MFI vs RSI 背离检测（反证信号）
        if rsi is not None:
            if rsi < 30 and mfi > rsi + 15:
                scores.append(("RSI超卖_MFI未确认(量价背离)", -1))
            elif rsi > 70 and mfi < rsi - 15:
                scores.append(("RSI超买_MFI未确认(量价背离)", 1))

    # 蜡烛形态评分
    if cdl_bullish:
        for name in cdl_bullish:
            scores.append((f"K线_{name}(看多)", 1))
    if cdl_bearish:
        for name in cdl_bearish:
            scores.append((f"K线_{name}(看空)", -1))

    total = sum(s for _, s in scores)
    rsi_detail = "oversold" if rsi and rsi < 30 else ("overbought" if rsi and rsi > 70 else "neutral")

    return {
        "symbol": symbol,
        "last_price": round(cl, 2),
        "last_date": str(df.index[-1].date()),
        "total_days": len(df),
        "ma_signals": ma_signals,
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
        "cdl_bullish": cdl_bullish,
        "cdl_bearish": cdl_bearish,
        # 原有
        "changes": changes,
        "resistance_90d": resistance,
        "support_90d": support,
        "scores": [{"label": l, "value": v} for l, v in scores],
        "total_score": total,
    }


def print_report(result: dict) -> None:
    """格式化打印分析报告。"""
    cl = result["last_price"]
    print("=" * 70)
    print(f"{result['symbol']} K线技术分析")
    print(f"数据: {result['total_days']} 天  |  最新: {result['last_date']}  |  收盘: ${cl:.2f}")
    print("=" * 70)

    # 均线
    print("\n🏷️  均线系统")
    for p in [5, 10, 20, 60, 120]:
        sig = result["ma_signals"].get(f"MA{p}")
        if sig:
            icon = "🟢 站上" if sig == "above" else "🔴 跌破"
            # 从 df 取实际值（这里用 result 中的 last 行）
            print(f"  MA{p:>4d}: {icon}")

    # RSI
    rsi = result["RSI"]
    if rsi is not None:
        labels = {70: "超买", 30: "超卖"}
        detail = result["RSI_detail"]
        print(f"\n📐 RSI(14): {rsi:.1f}  [{detail}]")

    # MACD
    if result["MACD"] is not None:
        print(f"\n📊 MACD")
        print(f"  MACD:     {result['MACD']:.3f}")
        print(f"  Signal:   {result['MACD_signal']:.3f}")
        print(f"  Hist:     {result['MACD_hist']:.3f}")
        zh = "金叉" if result["MACD_status"] == "golden_cross" else "死叉"
        print(f"  状态: {zh}")

    # 布林带
    if result["BB_mid"] is not None:
        print(f"\n📏 布林带 (20, 2σ)")
        print(f"  上轨: ${result['BB_upper']:.2f}")
        print(f"  中轨: ${result['BB_mid']:.2f}")
        print(f"  下轨: ${result['BB_lower']:.2f}")
        print(f"  位置: {result['BB_position']:.0f}% (0=下轨, 100=上轨)")

    # ATR
    if result["ATR"] is not None:
        print(f"\n📐 ATR(14): ${result['ATR']:.2f}  |  波动率: {result['ATR']/cl*100:.1f}%")

    # 成交量
    if result["vol_ratio"] is not None:
        print(f"\n📦 量比: {result['vol_ratio']:.2f}x")

    # ── 新增指标 ──
    # ADX
    if result["ADX"] is not None:
        trend_cn = {"trending": "趋势市", "ranging": "震荡市", "weak_trend": "弱趋势"}
        print(f"\n📐 ADX(14): {result['ADX']:.1f}  |  DI+: {result['DMP']:.1f}  DI-: {result['DMN']:.1f}  [{trend_cn.get(result['ADX_trend'], 'N/A')}]")

    # Stochastic
    if result["STOCH_k"] is not None:
        detail_cn = {"oversold": "超卖", "overbought": "超买", "neutral": "中性"}
        print(f"\n🎯 Stochastic(14,3,3): %K={result['STOCH_k']:.1f}  %D={result['STOCH_d']:.1f}  [{detail_cn.get(result['STOCH_detail'], 'N/A')}]")

    # SuperTrend
    if result["SUPERT_dir"] is not None:
        dir_str = "🟢 多头" if result["SUPERT_dir"] == 1 else "🔴 空头"
        stop_str = ""
        if result["SUPERT_dir"] == 1 and result["SUPERT_long_stop"] is not None:
            stop_str = f"  |  多头止损: ${result['SUPERT_long_stop']:.2f}"
        elif result["SUPERT_dir"] == -1 and result["SUPERT_short_stop"] is not None:
            stop_str = f"  |  空头止损: ${result['SUPERT_short_stop']:.2f}"
        print(f"\n📌 SuperTrend(10,3): {dir_str}{stop_str}")

    # OBV
    if result["OBV_divergence"] is not None:
        div_cn = {"bearish_divergence": "⚠️ 顶背离(看空反转预警)", "bullish_divergence": "✨ 底背离(看多反转预警)"}
        print(f"\n📊 OBV 背离检测: {div_cn.get(result['OBV_divergence'], 'N/A')}")

    # CCI
    if result["CCI"] is not None:
        detail_cn = {"extreme_overbought": "极度超买", "overbought": "超买", "extreme_oversold": "极度超卖", "oversold": "超卖", "normal": "正常"}
        print(f"\n📏 CCI(20): {result['CCI']:.1f}  [{detail_cn.get(result['CCI_detail'], 'N/A')}]")

    # MFI
    if result["MFI"] is not None:
        mfi_label = "超买" if result["MFI"] > 80 else ("超卖" if result["MFI"] < 20 else "中性")
        print(f"\n💰 MFI(14): {result['MFI']:.1f}  [{mfi_label}]")

    # 蜡烛形态
    if result["cdl_bullish"]:
        print(f"\n🕯️  K线反转信号(看多): {', '.join(result['cdl_bullish'])}")
    if result["cdl_bearish"]:
        print(f"\n🕯️  K线反转信号(看空): {', '.join(result['cdl_bearish'])}")

    # 涨跌幅
    if result["changes"]:
        print(f"\n📅 阶段涨跌")
        labels = {"5d": "5日", "1m": "1月", "3m": "3月", "6m": "半年", "1y": "年"}
        for k, v in result["changes"].items():
            emoji = "🟢" if v >= 0 else "🔴"
            print(f"  {labels.get(k, k):6s}: {emoji} {v:+.2f}%")

    # 关键价位
    print(f"\n📍 90天关键价位")
    print(f"  阻力: ${result['resistance_90d']:.2f}")
    print(f"  支撑: ${result['support_90d']:.2f}")
    print(f"  距阻力: {(result['resistance_90d'] - cl) / cl * 100:.1f}%")
    print(f"  距支撑: {(cl - result['support_90d']) / cl * 100:.1f}%")

    # 评分
    print(f"\n{'=' * 70}")
    print("🧠 综合评判")
    for s in result["scores"]:
        icon = "✅" if s["value"] > 0 else ("❌" if s["value"] < 0 else "➖")
        print(f"  {icon} {s['label']} ({s['value']:+d})")
    print(f"\n  综合评分: {result['total_score']:+d}")

    ts = result["total_score"]
    if ts >= 5:
        verdict = "强烈偏多"
    elif ts >= 2:
        verdict = "偏多"
    elif ts >= 0:
        verdict = "中性偏多"
    elif ts >= -3:
        verdict = "中性偏空"
    elif ts >= -6:
        verdict = "偏空"
    else:
        verdict = "强烈偏空"
    print(f"  判断: {verdict}")


def main():
    parser = argparse.ArgumentParser(description="K线技术分析")
    parser.add_argument("csv", nargs="?", default="data/MSFT.csv", help="CSV 文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-print", action="store_true", help="不打印报告")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"错误: 文件不存在 {csv_path}", file=sys.stderr)
        sys.exit(1)

    symbol = csv_path.stem.upper()
    df = load_data(str(csv_path))
    df = compute_all_indicators(df)
    result = analyze(df, symbol)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif not args.no_print:
        print_report(result)


if __name__ == "__main__":
    main()
