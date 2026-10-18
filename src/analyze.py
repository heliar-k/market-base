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

from src.indicators import MA_PERIODS, compute_all_indicators, detect_cdl_hits, load_data

# 抑制 pandas 逐列 insert 的性能警告（不影响正确性）
warnings.filterwarnings("ignore", message=".*frame\\.insert.*")


def _num(x, nd: int | None = None) -> float | None:
    """pd 值 → float，NaN → None；nd 给定则四舍五入。"""
    if not pd.notna(x):
        return None
    v = float(x)
    return round(v, nd) if nd is not None else v


def _int(x) -> int | None:
    """pd 值 → int，NaN → None。"""
    return int(x) if pd.notna(x) else None


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
    cl = float(last["close"])

    # ── 均线、斜率与偏离度 ──
    ma_signals = {}
    ma_values = {}
    ma_slopes = {}
    ma_dists = {}
    for p in MA_PERIODS:
        col = f"MA{p}"
        if col in df.columns and pd.notna(last.get(col)):
            ma_val = float(last[col])
            ma_signals[col] = "above" if cl > ma_val else "below"
            ma_values[col] = round(ma_val, 2)
            if ma_val != 0:
                ma_dists[col] = round((cl - ma_val) / ma_val * 100.0, 2)
            if len(df) >= 6 and pd.notna(df[col].iloc[-6]) and float(df[col].iloc[-6]) != 0:
                prev_ma = float(df[col].iloc[-6])
                ma_slopes[col] = round((ma_val - prev_ma) / prev_ma * 100.0, 2)

    # ── RSI ──
    rsi = _num(last.get("RSI"))

    # ── MACD ──
    macd = last.get("MACD")
    macd_signal = last.get("MACD_signal")
    macd_hist = last.get("MACD_hist")
    macd_status = (
        "golden_cross"
        if (pd.notna(macd) and pd.notna(macd_signal) and macd > macd_signal)
        else "dead_cross"
    )

    # ── 布林带 + Squeeze ──
    bb_upper = last.get("BB_upper")
    bb_lower = last.get("BB_lower")
    bb_mid = last.get("BB_mid")
    bb_pos = None
    if pd.notna(bb_upper) and pd.notna(bb_lower) and (bb_upper - bb_lower) > 0:
        bb_pos = (cl - float(bb_lower)) / (float(bb_upper) - float(bb_lower)) * 100.0
    bb_width = _num(last.get("BB_width"), 2)
    bb_squeeze = bool(_int(last.get("BB_squeeze")))

    # ── ATR ──
    atr = _num(last.get("ATR"))
    atr_pct = round(atr / cl * 100.0, 2) if (atr and cl > 0) else None

    # ── 成交量 ──
    vol_ratio = _num(last.get("vol_ratio"))

    # ── ADX/DMI ──
    adx = _num(last.get("ADX"))
    dmp = _num(last.get("DMP"))
    dmn = _num(last.get("DMN"))
    adx_trend = None
    if adx is not None:
        if adx > 25:
            adx_trend = "trending" if dmp is not None and dmn is not None else "strong"
        elif adx < 20:
            adx_trend = "ranging"
        else:
            adx_trend = "weak_trend"

    # ── Stochastic ──
    stoch_k = _num(last.get("STOCH_k"))
    stoch_d = _num(last.get("STOCH_d"))
    stoch_detail = None
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20 and stoch_d < 20:
            stoch_detail = "oversold"
        elif stoch_k > 80 and stoch_d > 80:
            stoch_detail = "overbought"
        else:
            stoch_detail = "neutral"

    # ── SuperTrend ──
    supert_dir = _int(last.get("SUPERT_dir"))
    supert_long = _num(last.get("SUPERT_long_stop"))
    supert_short = _num(last.get("SUPERT_short_stop"))

    # ── OBV 背离检测 ──
    obv = _num(last.get("OBV"))
    obv_divergence = None
    if obv is not None and len(df) >= 20:
        recent_20 = df.tail(20)
        price_20h = recent_20["close"].max()
        idx_price = recent_20["close"].idxmax()
        idx_obv = recent_20["OBV"].idxmax()
        if idx_price > idx_obv and price_20h > recent_20["close"].iloc[-10:].max() * 0.99:
            obv_divergence = "bearish_divergence"
        price_20l = recent_20["close"].min()
        idx_price_l = recent_20["close"].idxmin()
        idx_obv_l = recent_20["OBV"].idxmin()
        if idx_price_l > idx_obv_l and price_20l < recent_20["close"].iloc[-10:].min() * 1.01:
            obv_divergence = "bullish_divergence"

    # ── CCI ──
    cci = _num(last.get("CCI"))
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
    mfi = _num(last.get("MFI"))

    # ── 蜡烛形态（按 as_of 查命中，避免全量 attrs 的未来污染） ──
    cdl_bullish, cdl_bearish = detect_cdl_hits(df, as_of)

    # ── SMC (Smart Money Concepts) ──
    smc_structure = _int(last.get("SMC_structure"))
    smc_pd_zone = last.get("SMC_pd_zone", None)
    smc_premium = _num(last.get("SMC_premium"))
    smc_equilibrium = _num(last.get("SMC_equilibrium"))
    smc_discount = _num(last.get("SMC_discount"))

    recent_5 = df.tail(5)
    smc_recent_bos = int(recent_5["SMC_BOS"].sum()) if "SMC_BOS" in df.columns else 0
    smc_recent_choch = int(recent_5["SMC_CHoCH"].sum()) if "SMC_CHoCH" in df.columns else 0

    recent_10 = df.tail(10)
    smc_recent_fvg = int(recent_10["SMC_FVG"].abs().sum()) if "SMC_FVG" in df.columns else 0
    smc_recent_ob = int(recent_10["SMC_OB"].abs().sum()) if "SMC_OB" in df.columns else 0

    smc_sweep = _int(last.get("SMC_sweep"))
    smc_sweep_level = _num(last.get("SMC_sweep_level"))
    smc_recent_sweep = int(recent_10["SMC_sweep"].abs().sum()) if "SMC_sweep" in df.columns else 0

    smc_weekly_structure = _int(last.get("SMC_weekly_structure"))
    smc_htf_bias = last.get("SMC_htf_bias", None)

    # ── 近期涨跌 ──
    changes = {}
    if len(df) >= 2:
        prev_cl = float(df["close"].iloc[-2])
        if prev_cl != 0:
            changes["1d"] = round((cl - prev_cl) / prev_cl * 100.0, 2)
    for days, label in [(5, "5d"), (21, "1m"), (63, "3m"), (126, "6m"), (252, "1y")]:
        if len(df) >= days:
            base = float(df["close"].iloc[-days])
            if base != 0:
                changes[label] = round((cl - base) / base * 100.0, 2)

    # ── 关键价位 (90日 & 52周) ──
    recent = df.tail(90)
    resistance = float(recent["high"].max())
    support = float(recent["low"].min())

    win_52w = df.tail(252)
    high_52w = float(win_52w["high"].max())
    low_52w = float(win_52w["low"].min())
    pos_52w = (
        round((cl - low_52w) / (high_52w - low_52w) * 100.0, 1) if high_52w > low_52w else None
    )

    # ── 三维评分体系（趋势 / 动量 / 位置与反转）──
    trend_scores: list[tuple[str, int]] = []
    momentum_scores: list[tuple[str, int]] = []
    loc_scores: list[tuple[str, int]] = []

    # 1) 趋势维度 (Trend)
    ma20 = last.get("MA20")
    ma_mid_col = "MA50" if pd.notna(last.get("MA50")) else "MA60"
    ma_long_col = "MA200" if pd.notna(last.get("MA200")) else "MA120"
    ma_mid = last.get(ma_mid_col)
    ma_long = last.get(ma_long_col)
    stack_label = f"MA20>{ma_mid_col[2:]}>{ma_long_col[2:]}"
    bear_stack = f"MA20<{ma_mid_col[2:]}<{ma_long_col[2:]}"
    if pd.notna(ma20) and pd.notna(ma_mid) and pd.notna(ma_long):
        if cl > ma20 > ma_mid > ma_long:
            trend_scores.append((f"均线多头排列({stack_label})", 2))
        elif cl < ma20 < ma_mid < ma_long:
            trend_scores.append((f"均线空头排列({bear_stack})", -2))
        elif cl > ma_mid and ma20 > ma_mid:
            trend_scores.append((f"中期均线偏多(站上{ma_mid_col})", 1))
        elif cl < ma_mid and ma20 < ma_mid:
            trend_scores.append((f"中期均线偏空(跌破{ma_mid_col})", -1))
        else:
            trend_scores.append(("均线交织", 0))
    elif pd.notna(ma_mid):
        trend_scores.append(
            (
                f"价格站上中期均线({ma_mid_col})"
                if cl > ma_mid
                else f"价格跌破中期均线({ma_mid_col})",
                1 if cl > ma_mid else -1,
            )
        )

    if pd.notna(ma_long):
        trend_scores.append(
            (
                f"站上牛熊线({ma_long_col})" if cl > ma_long else f"跌破牛熊线({ma_long_col})",
                1 if cl > ma_long else -1,
            )
        )

    if adx is not None and dmp is not None and dmn is not None:
        if adx > 25:
            trend_scores.append(("ADX趋势强化", 1 if dmp > dmn else -1))
        elif adx < 20:
            trend_scores.append(("ADX震荡无趋势", 0))

    if supert_dir is not None:
        trend_scores.append(
            (
                "SuperTrend多头" if supert_dir == 1 else "SuperTrend空头",
                1 if supert_dir == 1 else -1,
            )
        )

    if smc_htf_bias == "strong_bullish":
        trend_scores.append(("周/日结构共振多头", 2))
    elif smc_htf_bias == "strong_bearish":
        trend_scores.append(("周/日结构共振空头", -2))
    elif smc_structure == 1:
        trend_scores.append(("日线结构偏多", 1))
    elif smc_structure == -1:
        trend_scores.append(("日线结构偏空", -1))

    if smc_recent_choch > 0:
        trend_scores.append(("近期CHoCH结构转多", 1))
    elif smc_recent_choch < 0:
        trend_scores.append(("近期CHoCH结构转空", -1))

    # 2) 动量维度 (Momentum) —— 去重：以 MACD + RSI 为核心，MFI 仅作背离确认
    if pd.notna(macd) and pd.notna(macd_signal) and pd.notna(macd_hist):
        momentum_scores.append(
            ("MACD金叉" if macd > macd_signal else "MACD死叉", 1 if macd > macd_signal else -1)
        )
        prev_hist = df["MACD_hist"].iloc[-2] if len(df) >= 2 else None
        if prev_hist is not None and pd.notna(prev_hist):
            if abs(float(macd_hist)) > abs(float(prev_hist)):
                momentum_scores.append(("MACD柱动能放大", 1 if macd_hist > 0 else -1))
            else:
                momentum_scores.append(("MACD柱动能衰减", 0))

    if rsi is not None:
        if 55 <= rsi <= 70:
            momentum_scores.append(("RSI多头动量区(55-70)", 1))
        elif 30 <= rsi <= 45:
            momentum_scores.append(("RSI空头动量区(30-45)", -1))
        else:
            momentum_scores.append(("RSI动量中性", 0))

    if obv_divergence == "bullish_divergence":
        momentum_scores.append(("OBV底背离(量先于价)", 2))
    elif obv_divergence == "bearish_divergence":
        momentum_scores.append(("OBV顶背离(量价背离)", -2))

    # 3) 位置与反转维度 (Location & Reversal) —— 合并超买超卖，不重复计分
    osc_oversold = (rsi is not None and rsi < 30) or (stoch_detail == "oversold")
    osc_overbought = (rsi is not None and rsi > 70) or (stoch_detail == "overbought")
    if osc_oversold:
        loc_scores.append(("震荡指标超卖(反弹窗口)", 1))
    elif osc_overbought:
        loc_scores.append(("震荡指标超买(回调压力)", -1))

    if rsi is not None and mfi is not None:
        if rsi < 35 and mfi > rsi + 15:
            loc_scores.append(("资金流底背离(MFI>RSI)", 1))
        elif rsi > 65 and mfi < rsi - 15:
            loc_scores.append(("资金流顶背离(MFI<RSI)", -1))

    if smc_pd_zone == "discount":
        loc_scores.append(("处于50日折价区(Discount)", 1))
    elif smc_pd_zone == "premium":
        loc_scores.append(("处于50日溢价区(Premium)", -1))

    if smc_recent_sweep > 0 and "SMC_sweep" in df.columns:
        last_sweeps = df[df["SMC_sweep"] != 0].tail(3)
        if not last_sweeps.empty:
            latest = int(last_sweeps["SMC_sweep"].iloc[-1])
            loc_scores.append(
                (
                    "下方流动性扫荡(吸筹)" if latest == 1 else "上方流动性扫荡(派发)",
                    1 if latest == 1 else -1,
                )
            )

    if cdl_bullish:
        loc_scores.append((f"看多K线形态({','.join(cdl_bullish[:2])})", 1))
    if cdl_bearish:
        loc_scores.append((f"看空K线形态({','.join(cdl_bearish[:2])})", -1))

    trend_score = sum(v for _, v in trend_scores)
    momentum_score = sum(v for _, v in momentum_scores)
    loc_score = sum(v for _, v in loc_scores)
    scores = [*trend_scores, *momentum_scores, *loc_scores]
    total = trend_score + momentum_score + loc_score

    rsi_detail = (
        "oversold" if rsi and rsi < 30 else ("overbought" if rsi and rsi > 70 else "neutral")
    )

    # ── 规则引擎结论合成（结论 / 依据 / 关键位与触发）──
    if trend_score >= 3:
        regime, regime_label = "bullish", "多头趋势"
    elif trend_score <= -3:
        regime, regime_label = "bearish", "空头趋势"
    elif trend_score >= 1:
        regime, regime_label = "mild_bullish", "温和偏多"
    elif trend_score <= -1:
        regime, regime_label = "mild_bearish", "温和偏空"
    else:
        regime, regime_label = "neutral", "震荡整理"

    if bb_squeeze:
        setup_tag = "波动率压缩(Squeeze)"
    elif osc_oversold and trend_score >= 0:
        setup_tag = "多头回调超卖"
    elif osc_overbought and trend_score <= 0:
        setup_tag = "反弹触及超买"
    elif obv_divergence == "bullish_divergence" or (smc_recent_choch > 0):
        setup_tag = "底部反转信号"
    elif obv_divergence == "bearish_divergence" or (smc_recent_choch < 0):
        setup_tag = "顶部背离预警"
    elif trend_score >= 3 and momentum_score >= 1:
        setup_tag = "趋势动量共振↑"
    elif trend_score <= -3 and momentum_score <= -1:
        setup_tag = "空头动量共振↓"
    else:
        setup_tag = regime_label

    # 三行式研判叙事（对齐 R.LINES_CONCLUSION: 结论 / 依据 / 触发）
    dist20_str = f"{ma_dists['MA20']:+.1f}%" if "MA20" in ma_dists else "—"
    dist200_str = f"{ma_dists['MA200']:+.1f}%" if "MA200" in ma_dists else "—"
    line1 = (
        f"{symbol} 当前处于「{regime_label}」（综合 {total:+d} 分 = "
        f"趋势 {trend_score:+d} / 动量 {momentum_score:+d} / 位置 {loc_score:+d}），"
        f"核心形态：{setup_tag}。"
    )
    ev_parts = [f"现价距 MA20 {dist20_str}、距 MA200 {dist200_str}"]
    if rsi is not None:
        ev_parts.append(f"RSI(14) {rsi:.1f}")
    ev_parts.append("MACD 金叉" if macd_status == "golden_cross" else "MACD 死叉")
    if adx is not None:
        ev_parts.append(f"ADX {adx:.1f} ({'趋势市' if adx > 25 else '震荡市'})")
    if bb_squeeze:
        ev_parts.append(f"布林带宽 {bb_width}% 处于近半年低位(Squeeze)")
    line2 = " · ".join(ev_parts) + "。"

    sup_ref = ma_values.get("MA50") or ma_values.get("MA60") or support
    line3 = (
        f"上方 90 日阻力 ${resistance:.2f}（52 周高点 ${high_52w:.2f}），"
        f"下方关键支撑 ${support:.2f}（中期均线参考 ${sup_ref:.2f}）；"
        f"日均波幅 ATR ${atr:.2f}（{atr_pct:.1f}%）。"
        if (atr is not None and atr_pct is not None)
        else f"上方 90 日阻力 ${resistance:.2f}，下方 90 日支撑 ${support:.2f}。"
    )

    return {
        "symbol": symbol,
        "last_price": round(cl, 2),
        "last_date": str(df.index[-1].date()),
        "total_days": len(df),
        # 结论与三维评分
        "regime": regime,
        "regime_label": regime_label,
        "setup_tag": setup_tag,
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "loc_score": loc_score,
        "summary_lines": [line1, line2, line3],
        "summary_text": "\n".join([line1, line2, line3]),
        # 均线
        "ma_signals": ma_signals,
        "ma_values": ma_values,
        "ma_slopes": ma_slopes,
        "ma_dists": ma_dists,
        # 动量与震荡
        "RSI": round(rsi, 1) if rsi is not None else None,
        "RSI_detail": rsi_detail,
        "MACD": _num(macd, 3),
        "MACD_signal": _num(macd_signal, 3),
        "MACD_hist": _num(macd_hist, 3),
        "MACD_status": macd_status,
        # 波动率与量能
        "BB_upper": _num(bb_upper, 2),
        "BB_lower": _num(bb_lower, 2),
        "BB_mid": _num(bb_mid, 2),
        "BB_position": round(bb_pos, 0) if bb_pos is not None else None,
        "BB_width": bb_width,
        "BB_squeeze": bb_squeeze,
        "ATR": round(atr, 2) if atr is not None else None,
        "ATR_pct": atr_pct,
        "vol_ratio": round(float(vol_ratio), 2) if vol_ratio is not None else None,
        # 趋势与扩展指标
        "ADX": round(adx, 1) if adx is not None else None,
        "DMP": round(dmp, 1) if dmp is not None else None,
        "DMN": round(dmn, 1) if dmn is not None else None,
        "ADX_trend": adx_trend,
        "STOCH_k": round(stoch_k, 1) if stoch_k is not None else None,
        "STOCH_d": round(stoch_d, 1) if stoch_d is not None else None,
        "STOCH_detail": stoch_detail,
        "SUPERT_dir": supert_dir,
        "SUPERT_long_stop": round(supert_long, 2) if supert_long is not None else None,
        "SUPERT_short_stop": round(supert_short, 2) if supert_short is not None else None,
        "OBV_divergence": obv_divergence,
        "CCI": round(cci, 1) if cci is not None else None,
        "CCI_detail": cci_detail,
        "MFI": round(mfi, 1) if mfi is not None else None,
        # SMC
        "SMC_structure": smc_structure,
        "SMC_pd_zone": smc_pd_zone,
        "SMC_premium": round(smc_premium, 2) if smc_premium is not None else None,
        "SMC_equilibrium": round(smc_equilibrium, 2) if smc_equilibrium is not None else None,
        "SMC_discount": round(smc_discount, 2) if smc_discount is not None else None,
        "SMC_recent_BOS": smc_recent_bos,
        "SMC_recent_CHoCH": smc_recent_choch,
        "SMC_recent_FVG": smc_recent_fvg,
        "SMC_recent_OB": smc_recent_ob,
        "SMC_sweep": smc_sweep,
        "SMC_sweep_level": round(smc_sweep_level, 2) if smc_sweep_level is not None else None,
        "SMC_recent_sweep": smc_recent_sweep,
        "SMC_weekly_structure": smc_weekly_structure,
        "SMC_htf_bias": smc_htf_bias,
        "cdl_bullish": cdl_bullish,
        "cdl_bearish": cdl_bearish,
        # 涨跌与关键位
        "changes": changes,
        "resistance_90d": round(resistance, 2),
        "support_90d": round(support, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pos_52w": pos_52w,
        "scores": [{"label": lb, "value": v} for lb, v in scores],
        "trend_scores": [{"label": lb, "value": v} for lb, v in trend_scores],
        "momentum_scores": [{"label": lb, "value": v} for lb, v in momentum_scores],
        "loc_scores": [{"label": lb, "value": v} for lb, v in loc_scores],
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
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认行为，保留兼容）")
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
