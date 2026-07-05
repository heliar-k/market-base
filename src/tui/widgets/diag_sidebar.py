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


def _format_diagnosis(r: dict) -> str:
    """把 analyze dict 渲染成多行文本。"""
    lines: list[str] = []
    sym = r.get("symbol", "?")
    lines.append(f"{sym}  ${_fmt(r.get('last_price'))}  {r.get('last_date', '?')}")
    lines.append("")

    # 综合评分
    total = r.get("total_score", 0)
    lines.append(f"综合评分: {total:+d}")
    scores = r.get("scores") or []
    if scores:
        # 只列有非零分的信号，最多 8 条，避免刷屏
        nonzero = [s for s in scores if s.get("value")]
        for s in nonzero[:8]:
            v = s.get("value", 0)
            mark = "▲" if v > 0 else "▼"
            lines.append(f"  {mark} {s.get('label', '')} ({v:+d})")
    lines.append("")

    # 均线方向
    ma_signals = r.get("ma_signals") or {}
    if ma_signals:
        ma_parts = [
            f"MA{p}:{'上' if ma_signals.get(f'MA{p}') == 'above' else '下'}"
            for p in (5, 10, 20, 60, 120)
            if f"MA{p}" in ma_signals
        ]
        lines.append("均线: " + " ".join(ma_parts))
    lines.append("")

    # RSI / MACD
    rsi = r.get("RSI")
    lines.append(f"RSI: {_fmt(rsi)} ({r.get('RSI_detail', '—')})")
    macd_status = r.get("MACD_status")
    macd_mark = (
        "金叉"
        if macd_status == "golden_cross"
        else "死叉"
        if macd_status == "dead_cross"
        else "—"
    )
    lines.append(
        f"MACD: {_fmt(r.get('MACD'))} | sig {_fmt(r.get('MACD_signal'))} | {macd_mark}"
    )
    lines.append("")

    # ADX 趋势
    adx = r.get("ADX")
    adx_trend = r.get("ADX_trend", "—")
    lines.append(f"ADX: {_fmt(adx)} ({adx_trend})")
    lines.append("")

    # Stoch / CCI / MFI（精简）
    stoch = r.get("STOCH_detail")
    if stoch:
        lines.append(
            f"Stoch: %K {_fmt(r.get('STOCH_k'))} %D {_fmt(r.get('STOCH_d'))} ({stoch})"
        )
    cci_detail = r.get("CCI_detail")
    if cci_detail:
        lines.append(f"CCI: {_fmt(r.get('CCI'))} ({cci_detail})")
    mfi = r.get("MFI")
    if mfi is not None:
        lines.append(f"MFI: {_fmt(mfi)}")
    lines.append("")

    # 形态命中
    bull = r.get("cdl_bullish") or []
    bear = r.get("cdl_bearish") or []
    if bull or bear:
        lines.append("K线形态:")
        if bull:
            lines.append("  ▲ " + ", ".join(bull[:5]))
        if bear:
            lines.append("  ▼ " + ", ".join(bear[:5]))
        lines.append("")

    # 关键价位
    lines.append(f"支撑(90d): {_fmt(r.get('support_90d'))}")
    lines.append(f"阻力(90d): {_fmt(r.get('resistance_90d'))}")

    return "\n".join(lines)
