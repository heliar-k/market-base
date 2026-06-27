#!/usr/bin/env python3
"""
K线技术分析脚本 — 读取本地 CSV 并输出完整技术分析报告。

用法:
    python -m code.analyze                            # 分析 data/MSFT.csv
    python -m code.analyze data/AAPL.csv              # 分析指定文件
    python -m code.analyze data/MSFT.csv --no-print   # 仅输出最新行 JSON
"""

import argparse
import json
import warnings
from pathlib import Path

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich import box

from code.indicators import load_data, compute_all_indicators

# 抑制 pandas 逐列 insert 的性能警告（不影响正确性）
warnings.filterwarnings("ignore", message=".*frame\\.insert.*")

console = Console()


def analyze(df: pd.DataFrame, symbol: str) -> dict:
    """从带指标的 DataFrame 提取诊断结果。"""
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
            scores.append(("MA多头排列", 2))
        elif ma5 < ma20 < ma60:
            scores.append(("MA空头排列", -2))
        else:
            scores.append(("MA交织", 0))
    if pd.notna(ma60):
        scores.append(("价格站上MA60" if cl > ma60 else "价格跌破MA60", 1 if cl > ma60 else -1))

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
        scores.append(("SuperTrend多头" if supert_dir == 1 else "SuperTrend空头", 1 if supert_dir == 1 else -1))

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

    total = sum(s for _, s in scores)
    rsi_detail = "oversold" if rsi and rsi < 30 else ("overbought" if rsi and rsi > 70 else "neutral")

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
    """使用 Rich 格式化打印分析报告。"""
    cl = result["last_price"]
    total = result["total_score"]
    ma_vals = result.get("ma_values", {})

    # ── 顶部面板 ──
    if total >= 5:
        verdict, vstyle = "强烈偏多 🚀", "bold spring_green3"
    elif total >= 2:
        verdict, vstyle = "偏多 📈", "spring_green3"
    elif total >= 0:
        verdict, vstyle = "中性偏多 ➡️", "light_goldenrod2"
    elif total >= -3:
        verdict, vstyle = "中性偏空 ⬅️", "light_goldenrod2"
    elif total >= -6:
        verdict, vstyle = "偏空 📉", "indian_red"
    else:
        verdict, vstyle = "强烈偏空 💥", "bold indian_red"

    header = Text.assemble(
        (f"{result['symbol']}", "bold cyan"),
        "  技术分析报告\n",
        (f"{result['last_date']}", "dim"),
        f"  收盘: ",
        (f"${cl:,.2f}", "bold"),
        f"  |  {result['total_days']} 天数据\n",
        f"综合评分: ",
        (f"{total:+d}", vstyle),
        f"  →  ",
        (verdict, vstyle),
    )
    console.print(Panel(header, expand=False))

    # ═══ 工具函数 ═══
    def _section(title: str) -> None:
        console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="dim"))

    # ═══ 颜色 ═══
    BULL = "spring_green3"
    BEAR = "indian_red"
    WARN = "light_goldenrod2"

    # ── 趋势指标 ──
    _section("趋势指标")

    # 均线表格
    ma_table = Table(box=box.SIMPLE, show_header=True, header_style="bold", expand=False)
    ma_table.add_column("均线", style="dim", width=8)
    ma_table.add_column("价格", justify="right", width=12)
    ma_table.add_column("方向", justify="center", width=6)
    ma_table.add_column("偏离", justify="right", width=10)

    for p in [5, 10, 20, 60, 120]:
        sig = result["ma_signals"].get(f"MA{p}")
        ma_v = ma_vals.get(f"MA{p}")
        if sig and ma_v is not None:
            pct = (cl - ma_v) / ma_v * 100
            direction = f"[{BULL}]▲ 站上[/{BULL}]" if sig == "above" else f"[{BEAR}]▼ 跌破[/{BEAR}]"
            pct_str = f"[{BULL}]+{pct:.1f}%[/{BULL}]" if pct >= 0 else f"[{BEAR}]{pct:.1f}%[/{BEAR}]"
            ma_table.add_row(f"MA{p}", f"${ma_v:,.2f}", direction, pct_str)
    console.print(ma_table)

    # ADX
    if result["ADX"] is not None:
        trend_cn = {"trending": "趋势市", "ranging": "震荡市", "weak_trend": "弱趋势", "strong": "强势"}
        adx, dmp, dmn = result["ADX"], result["DMP"], result["DMN"]
        dir_color = BULL if dmp > dmn else (BEAR if dmn > dmp else "")
        dir_str = "多头占优" if dmp > dmn else ("空头占优" if dmn > dmp else "势均力敌")
        if dir_color:
            console.print(
                f"  [bold]ADX(14)[/bold] {adx:.1f} [{trend_cn.get(result['ADX_trend'], 'N/A')}]  |  "
                f"[{dir_color}]DI+ {dmp:.1f}  DI- {dmn:.1f}  {dir_str}[/{dir_color}]"
            )
        else:
            console.print(
                f"  [bold]ADX(14)[/bold] {adx:.1f} [{trend_cn.get(result['ADX_trend'], 'N/A')}]  |  "
                f"DI+ {dmp:.1f}  DI- {dmn:.1f}  {dir_str}"
            )

    # SuperTrend
    if result["SUPERT_dir"] is not None:
        st_dir = f"[{BULL}]多头[/{BULL}]" if result["SUPERT_dir"] == 1 else f"[{BEAR}]空头[/{BEAR}]"
        st_price = result.get("SUPERT_long_stop") if result["SUPERT_dir"] == 1 else result.get("SUPERT_short_stop")
        st_str = f"  止损: ${st_price:,.2f}" if st_price else ""
        console.print(f"  [bold]SuperTrend(10,3)[/bold] {st_dir}{st_str}")

    # ── 动量指标 ──
    _section("动量指标")

    mom_t = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    mom_t.add_column("label", style="bold", width=16)
    mom_t.add_column("value")

    if result["RSI"] is not None:
        rsi = result["RSI"]
        rsi_lvl = "超买" if rsi > 70 else ("超卖" if rsi < 30 else ("偏强" if rsi >= 60 else ("偏弱" if rsi <= 40 else "中性")))
        c = BEAR if rsi > 70 else (BULL if rsi < 30 else "")
        v = f"[{c}]{rsi:.1f} {rsi_lvl}[/{c}]" if c else f"{rsi:.1f} {rsi_lvl}"
        mom_t.add_row("RSI(14)", v)

    if result["STOCH_k"] is not None:
        sk, sd = result["STOCH_k"], result["STOCH_d"]
        stoch_lvl = {"oversold": f"[{BULL}]超卖[/{BULL}]", "overbought": f"[{BEAR}]超买[/{BEAR}]", "neutral": "中性"}.get(result["STOCH_detail"], "")
        ac = BULL if sk > sd else BEAR
        arrow = "↑" if sk > sd else "↓"
        mom_t.add_row("Stoch(14,3,3)", f"%K={sk:.1f}  %D={sd:.1f}  [{ac}]{arrow}[/{ac}] {stoch_lvl}")

    if result["MACD"] is not None:
        macd_status = "金叉" if result["MACD_status"] == "golden_cross" else "死叉"
        mc = BULL if result["MACD_status"] == "golden_cross" else BEAR
        hist = result["MACD_hist"]
        hc = BULL if hist > 0 else BEAR
        ha = "▲" if hist > 0 else "▼"
        mom_t.add_row("MACD", f"DIF={result['MACD']:.3f}  DEA={result['MACD_signal']:.3f}  "
                      f"Hist: [{hc}]{ha} {hist:.3f}[/{hc}]  [{mc}]{macd_status}[/{mc}]")

    if result["CCI"] is not None:
        cci = result["CCI"]
        cci_lvl = {"extreme_overbought": f"[{BEAR}]极度超买[/{BEAR}]", "overbought": f"[{BEAR}]超买[/{BEAR}]",
                    "extreme_oversold": f"[{BULL}]极度超卖[/{BULL}]", "oversold": f"[{BULL}]超卖[/{BULL}]",
                    "normal": "正常"}.get(result["CCI_detail"], "")
        cc = BEAR if cci > 100 else (BULL if cci < -100 else "")
        v = f"[{cc}]{cci:.1f}[/{cc}]" if cc else f"{cci:.1f}"
        mom_t.add_row("CCI(20)", f"{v}  [{cci_lvl}]")

    console.print(mom_t)

    # ── 波动与量价 ──
    _section("波动与量价")

    vol_t = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    vol_t.add_column("label", style="bold", width=16)
    vol_t.add_column("value")

    if result["BB_mid"] is not None:
        bb_pos = result["BB_position"]
        bb_lvl = "[dim]偏弱[/dim]" if bb_pos < 30 else ("[bold]偏强[/bold]" if bb_pos > 70 else "中性")
        vol_t.add_row("布林带(20,2σ)",
                      f"上 [{BEAR}]${result['BB_upper']:,.2f}[/{BEAR}]  "
                      f"中 [dim]${result['BB_mid']:,.2f}[/dim]  "
                      f"下 [{BULL}]${result['BB_lower']:,.2f}[/{BULL}]")
        vol_t.add_row("  价格位置", f"{bb_pos:.0f}% [{bb_lvl}]  (0=下轨, 100=上轨)")

    if result["ATR"] is not None:
        atr_pct = result["ATR"] / cl * 100
        vol_t.add_row("ATR(14)", f"${result['ATR']:,.2f}  日波动率: {atr_pct:.1f}%")

    if result["MFI"] is not None:
        mfi = result["MFI"]
        mfi_lvl = f"[{BEAR}]超买[/{BEAR}]" if mfi > 80 else (f"[{BULL}]超卖[/{BULL}]" if mfi < 20 else "中性")
        mc = BEAR if mfi > 80 else (BULL if mfi < 20 else "")
        v = f"[{mc}]{mfi:.1f}[/{mc}]" if mc else f"{mfi:.1f}"
        vol_t.add_row("MFI(14)", f"{v}  [{mfi_lvl}]")

    if result["vol_ratio"] is not None:
        vr = result["vol_ratio"]
        vr_str = "放量" if vr > 1.5 else ("缩量" if vr < 0.5 else "正常")
        vc = WARN if vr > 1.5 else (BULL if vr < 0.5 else "")
        v = f"[{vc}]{vr:.2f}x [{vr_str}][/{vc}]" if vc else f"{vr:.2f}x [{vr_str}]"
        vol_t.add_row("量比", v)

    console.print(vol_t)

    if result["OBV_divergence"] is not None:
        div_text = {"bearish_divergence": f"[{BEAR}]⚠ 顶背离 — 价格新高但量能不跟[/{BEAR}]",
                    "bullish_divergence": f"[{BULL}]✨ 底背离 — 价格新低但量能企稳[/{BULL}]"}
        console.print(f"  {div_text.get(result['OBV_divergence'], 'N/A')}")

    # ── 蜡烛形态 ──
    if result["cdl_bullish"] or result["cdl_bearish"]:
        _section("K线形态")
        if result["cdl_bullish"]:
            console.print(f"  [{BULL}]看多:[/{BULL}] {', '.join(result['cdl_bullish'])}")
        if result["cdl_bearish"]:
            console.print(f"  [{BEAR}]看空:[/{BEAR}] {', '.join(result['cdl_bearish'])}")

    # ── 阶段涨跌 ──
    if result["changes"]:
        _section("阶段涨跌")
        labels = {"5d": "5日", "1m": "1月", "3m": "3月", "6m": "半年", "1y": "年度"}
        chg_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold dim", padding=(0, 2))
        headers = []
        values = []
        for k, v in result["changes"].items():
            chg_table.add_column(labels.get(k, k), justify="center")
            c = BULL if v >= 0 else BEAR
            arrow = "▲" if v >= 0 else "▼"
            values.append(f"[{c}]{arrow} {v:+.2f}%[/{c}]")
        chg_table.add_row(*values)
        console.print(chg_table)

    # ── 关键价位 ──
    _section("关键价位 (90日)")
    res, sup = result["resistance_90d"], result["support_90d"]
    srz = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold dim", padding=(0, 2))
    srz.add_column("类型")
    srz.add_column("价位", justify="right")
    srz.add_column("距当前", justify="right")
    srz.add_row("阻力", f"[{BEAR}]${res:,.2f}[/{BEAR}]", f"{(res - cl) / cl * 100:+.1f}%")
    srz.add_row("支撑", f"[{BULL}]${sup:,.2f}[/{BULL}]", f"{(sup - cl) / cl * 100:+.1f}%")
    console.print(srz)

    # ── 评分明细 ──
    _section("评分明细")
    groups = {
        "趋势": ["MA多头", "MA空头", "MA交织", "价格", "SuperTrend", "ADX"],
        "动量": ["RSI", "MACD", "Stoch", "CCI"],
        "量价": ["MFI", "OBV", "量价"],
        "形态": ["K线"],
    }
    for cat, prefixes in groups.items():
        items = [s for s in result["scores"] if any(s["label"].startswith(p) for p in prefixes)]
        if not items:
            continue
        parts = []
        for s in items:
            if s["value"] > 0:
                icon, color = "+", BULL
            elif s["value"] < 0:
                icon, color = "-", BEAR
            else:
                icon, color = "○", "dim"
            parts.append(f"[{color}]{icon}{s['label']} ({s['value']:+d})[/{color}]")
        console.print(f"  [bold dim]{cat}[/]  {' │ '.join(parts)}")

    # ── 底部结论 ──
    console.print()
    console.print(f"  [bold]总分: {total:+d}  →  [{vstyle}]{verdict}[/{vstyle}]")
    console.print()


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
