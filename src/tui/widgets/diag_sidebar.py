"""DiagSidebar — 诊断侧栏 widget：渲染 analyze() 返回的 dict 成文本。

从 dict 重新设计渲染（不是 Rich report）。关键字段：综合评分、MA 方向、RSI/MACD 状态、
ADX 趋势、形态命中、关键价位。
"""

from __future__ import annotations

from textual.widgets import Static


class DiagSidebar(Static):
    """诊断侧栏：把 analyze() dict 渲染成可读文本。"""

    def render_diagnosis(self, result: dict) -> None:
        """把 analyze dict 渲染成文本（评分/MA/RSI/MACD/ADX/形态/价位）。"""
        self.update(_format_diagnosis(result))


def _fmt(v: object) -> str:
    """格式化数值/None 为展示串。"""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _score_bar(score: int, max_score: int = 12) -> str:
    """用色块表示评分强度。"""
    if score >= 6:
        color = "#3fb950"  # green
        bar = "█" * min(score, max_score)
    elif score >= 3:
        color = "#d2991d"  # yellow
        bar = "█" * score + "░" * (max_score - score)
    elif score > 0:
        color = "#f85149"  # red
        bar = "█" * score + "░" * (max_score - score)
    elif score == 0:
        color = "#8b949e"
        bar = "░" * max_score
    else:
        color = "#f85149"
        bar = "█" * min(abs(score), max_score)
    return f"[{color}]{bar}[/]"


def _sign(v: float) -> str:
    """数值符号着色：正绿负红。"""
    if v > 0:
        return f"[#3fb950]+{v}[/]"
    elif v < 0:
        return f"[#f85149]{v}[/]"
    return "[#8b949e]0[/]"


def _format_diagnosis(r: dict) -> str:
    """把 analyze dict 渲染成 Rich markup 多行文本。"""
    lines: list[str] = []
    sym = r.get("symbol", "?")
    price = r.get("last_price")
    price_str = _fmt(price)
    last_date = r.get("last_date", "?")

    # ── 标题行 ──
    lines.append(
        f"[bold #58a6ff]{sym}[/]  [bold #c9d1d9]${price_str}[/]  [dim]{last_date}[/]"
    )
    lines.append("[dim]" + "─" * 32 + "[/]")

    # ── 综合评分 ──
    total = r.get("total_score", 0)
    bar = _score_bar(total)
    lines.append(f"[bold]综合评分:[/] {_sign(total)}  {bar}")
    scores = r.get("scores") or []
    if scores:
        nonzero = [s for s in scores if s.get("value")]
        for s in nonzero[:8]:
            v = s.get("value", 0)
            mark = "[#3fb950]▲[/]" if v > 0 else "[#f85149]▼[/]"
            lines.append(f"  {mark} [dim]{s.get('label', '')}[/] ({_sign(v)})")
    lines.append("")

    # ── 均线方向 ──
    ma_signals = r.get("ma_signals") or {}
    if ma_signals:
        ma_parts = []
        for p in (5, 10, 20, 60, 120):
            key = f"MA{p}"
            if key in ma_signals:
                direction = ma_signals[key]
                if direction == "above":
                    ma_parts.append(f"[#3fb950]MA{p}↑[/]")
                else:
                    ma_parts.append(f"[#f85149]MA{p}↓[/]")
        lines.append("[bold]均线:[/] " + " ".join(ma_parts))
    lines.append("")

    # ── RSI / MACD ──
    rsi = r.get("RSI")
    rsi_str = _fmt(rsi)
    rsi_detail = r.get("RSI_detail", "—")
    # RSI 着色：超买红，超卖绿
    if isinstance(rsi, (int, float)):
        if rsi >= 70:
            rsi_color = "#f85149"
        elif rsi <= 30:
            rsi_color = "#3fb950"
        else:
            rsi_color = "#c9d1d9"
        lines.append(f"[bold]RSI:[/] [{rsi_color}]{rsi_str}[/] [dim]({rsi_detail})[/]")
    else:
        lines.append(f"[bold]RSI:[/] {rsi_str} [dim]({rsi_detail})[/]")

    macd_status = r.get("MACD_status")
    if macd_status == "golden_cross":
        macd_mark = "[#3fb950]金叉 ↑[/]"
    elif macd_status == "dead_cross":
        macd_mark = "[#f85149]死叉 ↓[/]"
    else:
        macd_mark = "[dim]—[/]"
    lines.append(
        f"[bold]MACD:[/] {_fmt(r.get('MACD'))} "
        f"[dim]| sig {_fmt(r.get('MACD_signal'))} |[/] {macd_mark}"
    )
    lines.append("")

    # ── ADX 趋势 ──
    adx = r.get("ADX")
    adx_trend = r.get("ADX_trend", "—")
    if isinstance(adx, (int, float)) and adx >= 25:
        trend_color = "#3fb950" if adx_trend in ("bullish", "uptrend") else "#f85149"
        lines.append(
            f"[bold]ADX:[/] [{trend_color}]{_fmt(adx)}[/] [dim]({adx_trend})[/]"
        )
    else:
        lines.append(f"[bold]ADX:[/] {_fmt(adx)} [dim]({adx_trend})[/]")
    lines.append("")

    # ── Stoch / CCI / MFI ──
    stoch = r.get("STOCH_detail")
    if stoch:
        lines.append(
            f"[bold]Stoch:[/] %K {_fmt(r.get('STOCH_k'))} "
            f"%D {_fmt(r.get('STOCH_d'))} [dim]({stoch})[/]"
        )
    cci_detail = r.get("CCI_detail")
    if cci_detail:
        lines.append(f"[bold]CCI:[/] {_fmt(r.get('CCI'))} [dim]({cci_detail})[/]")
    mfi = r.get("MFI")
    if mfi is not None:
        lines.append(f"[bold]MFI:[/] {_fmt(mfi)}")
    lines.append("")

    # ── K线形态 ──
    bull = r.get("cdl_bullish") or []
    bear = r.get("cdl_bearish") or []
    if bull or bear:
        lines.append("[bold]K线形态:[/]")
        if bull:
            lines.append("  [#3fb950]▲[/] " + ", ".join(bull[:5]))
        if bear:
            lines.append("  [#f85149]▼[/] " + ", ".join(bear[:5]))
        lines.append("")

    # ── 关键价位 ──
    lines.append("[dim]" + "─" * 32 + "[/]")
    lines.append(f"[bold]支撑(90d):[/] [#3fb950]{_fmt(r.get('support_90d'))}[/]")
    lines.append(f"[bold]阻力(90d):[/] [#f85149]{_fmt(r.get('resistance_90d'))}[/]")

    return "\n".join(lines)
