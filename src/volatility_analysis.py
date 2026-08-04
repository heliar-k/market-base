"""波动率研判规则引擎 — 复刻 timsun.net/volatility 风格的确定性研判生成。

规则引擎先行：全部结论由本地 CSV（data/cboe/volatility.csv：VIX1D/VIX9D/VIX/
VIX3M/VIX6M/VIX1Y/SKEW/OVX）确定性推导，无 LLM 依赖、可测试。

LLM 预留：`generate_volatility_analysis()` 是唯一入口，内部先尝试 `_llm_generate()`
（当前返回 None = 未启用），None 则回落规则引擎。接好 LLM 后只需实现
`_llm_generate()` 返回同构 dict，调用方（server.py /api/volatility/analysis）零改动。

输出结构：
  vix:            首页卡片（最新值 / 日周变动 / 分位 / 区间）
  signals:        波动率信号三段文（VIX 水平 / 期限结构 / 展望）
  term_structure: 期限结构图（1D/9D/30D/3M/6M）+ contango 徽章
  vix_history:    VIX + SKEW 近 6 个月双线（子页主图，SKEW 子图复用同一序列）
  vix_skew_scatter: VIX×SKEW 散点（近 3 个月）
  recent:         近期数据最近 20 条
"""

from __future__ import annotations

import pandas as pd

from src.analysis_utils import chg_pct as _chg_pct
from src.analysis_utils import chg_prev as _chg_prev
from src.analysis_utils import latest as _latest
from src.analysis_utils import read_csv_or_empty, zone
from src.config import ROOT

# 区间阈值（VIX 水平参考，与 timsun 一致）
ZONES = [
    ("平静", 0, 15, "#34d399"),
    ("正常", 15, 25, "#f59e0b"),
    ("警戒", 25, 35, "#f97316"),
    ("恐慌", 35, 1e9, "#f87171"),
]


def _read() -> pd.DataFrame:
    return read_csv_or_empty(ROOT / "data" / "cboe" / "volatility.csv")


def _percentile(s: pd.Series, window: int | None = None) -> float | None:
    """当前值在序列（可选最近 window 个交易日）中的分位 %。"""
    s = s.dropna()
    if s.empty:
        return None
    if window is not None:
        s = s.tail(window)
    cur = s.iloc[-1]
    return round((s <= cur).mean() * 100, 1)


def _slope_label(slope: float) -> str:
    if slope > 1:
        return "陡峭 contango"
    if slope > 0:
        return "温和 contango"
    if slope > -1:
        return "温和 backwardation"
    return "陡峭 backwardation"


def _chg_pts(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 个交易日前的变化（点数）。"""
    pair = _chg_prev(s, n)
    return None if pair is None else round(pair[0] - pair[1], 2)


def vix_card(df: pd.DataFrame) -> dict:
    """首页 VIX 卡片：最新值 / 日周变动 / 分位 / 区间徽章。"""
    s = df["VIX"].dropna()
    if s.empty:
        return {}
    v = float(s.iloc[-1])
    zone_name, color = zone(v, ZONES)
    return {
        "value": v,
        "as_of": s.index[-1].strftime("%Y-%m-%d"),
        "chg_1d_pct": _chg_pct(s, 1),
        "chg_1w_pct": _chg_pct(s, 5),
        # 点数口径（对齐 timsun 卡片「7 日变化 −2.84pt」，审计 P2-②）
        "chg_1w": _chg_pts(s, 5),
        "percentile_1y": _percentile(s, 250),
        "zone": zone_name,
        "zone_color": color,
    }


def term_structure(df: pd.DataFrame) -> dict:
    """期限结构 1D/9D/30D/3M/6M 最新五点 + contango 状态。"""
    labels = ["1D", "9D", "30D", "3M", "6M"]
    cols = ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX6M"]
    values = []
    for c in cols:
        s = df[c].dropna()
        values.append(round(float(s.iloc[-1]), 2) if not s.empty else None)
    slope = _latest(df["VIX_TERM_SLOPE"]) if "VIX_TERM_SLOPE" in df else None
    if slope is None:
        state = "—"
    else:
        state = "contango" if slope >= 0 else "backwardation"
    return {
        "labels": labels,
        "values": values,
        "slope": round(slope, 2) if slope is not None else None,
        "state": state,
        "slope_label": _slope_label(slope) if slope is not None else "—",
    }


def signal_vix_level(card: dict) -> str:
    """信号一：VIX 水平（值 / 变动 / 分位 / 区间判断）。"""
    parts = [f"VIX 收于 {card['value']}"]
    if card["chg_1d_pct"] is not None:
        parts.append(
            f"日{'涨' if card['chg_1d_pct'] >= 0 else '跌'} "
            f"{abs(card['chg_1d_pct']):.2f}%"
        )
    if card["chg_1w_pct"] is not None:
        parts.append(
            f"周{'涨' if card['chg_1w_pct'] >= 0 else '跌'} "
            f"{abs(card['chg_1w_pct']):.2f}%"
        )
    zone_name, _ = zone(card["value"], ZONES)
    pct = card.get("percentile_1y")
    pct_txt = f"，处于近一年 {pct}% 分位" if pct is not None else ""
    text = (
        "，".join(parts) + f"。绝对值处于{zone_name}区间"
        "（<15 平静 / 15-25 正常 / 25-35 警戒 / >35 恐慌）" + pct_txt + "。"
    )
    # 趋势注解
    if card["chg_1w_pct"] is not None and card["chg_1w_pct"] <= -10:
        text += (
            "周跌幅超 10% 表明风险溢价正在快速消退，但绝对值未跌破 12 的"
            "超低波区域，需确认下行动能是否持续。"
        )
    elif card["chg_1w_pct"] is not None and card["chg_1w_pct"] >= 10:
        text += "周涨幅超 10% 说明市场恐慌情绪升温，关注 25 上方警戒区的持续性。"
    elif card["value"] < 15:
        text += "低于 15 属于低波动区，市场情绪平稳，警惕低波动本身积累的下行风险。"
    else:
        text += "处于历史中位附近，风险溢价定价中性。"
    return text


def signal_term(term: dict) -> str:
    """信号二：期限结构（形态 / 近远端对比 / 前端隐含）。"""
    state = term["state"]
    if state == "—":
        return "期限结构斜率缺失，暂不判断 contango/backwardation。"
    ts = term["values"]
    text = (
        f"波动率期限呈{'标准 ' if state == 'contango' else ''}{state}"
        f"（{term['slope_label']}"
    )
    if term["slope"] is not None:
        text += f"，VIX−VIX9D={term['slope']:+.2f}"
    text += "），"
    if state == "contango":
        text += (
            f"近端 1D {ts[0]}、9D {ts[1]}、30D {ts[2]} 低于远端 "
            f"3M {ts[3]}、6M {ts[4]}，"
            "近低远高说明市场对近期风险的定价温和，而中长期不确定性仍存。"
        )
        if ts[0] is not None and ts[0] < 10:
            text += (
                "前端 VIX1D 处个位数，意味着市场预期未来一日实际波动极低，"
                "若出现突发性负面新闻，隐含波动可能急速上升。"
            )
    else:
        text += (
            "近高远低，市场正为近期尾部风险支付溢价，期限结构倒挂通常伴随高波动阶段。"
        )
    return text


def signal_outlook(df: pd.DataFrame, card: dict) -> str:
    """信号三：展望（VIX9D 方向 + OVX 状态 + SKEW 尾部风险合成）。"""
    vix9 = df["VIX9D"].dropna()
    ovx = df["OVX"].dropna() if "OVX" in df else pd.Series(dtype=float)
    skew = df["SKEW"].dropna() if "SKEW" in df else pd.Series(dtype=float)

    v9 = float(vix9.iloc[-1]) if not vix9.empty else None
    v9_chg = _chg_pct(vix9, 5)
    ov = float(ovx.iloc[-1]) if not ovx.empty else None
    sk = float(skew.iloc[-1]) if not skew.empty else None

    center = card["value"]
    parts = [
        f"未来一周 VIX 大概率在 {max(0, center - 3):.0f}-{center + 3:.0f} 区间波动"
    ]
    if v9 is not None:
        if v9_chg is None:
            parts.append(f"前端 VIX9D {v9:.1f}，短期情绪温和")
        else:
            dirn = "上行" if v9_chg > 0 else "下行"
            parts.append(
                f"前端 VIX9D {v9:.1f} 近一周{dirn}，"
                f"短期情绪偏{'谨慎' if v9_chg > 0 else '温和'}"
            )
    if ov is not None:
        parts.append(
            f"OVX（原油波动率）{ov:.1f} "
            + ("仍处高位，油价波动是潜在脉冲源" if ov > 50 else "处于温和水平")
        )
    if sk is not None:
        if sk >= 140:
            skew_txt = "处 140 上方，尾部对冲需求偏高"
        elif sk >= 130:
            skew_txt = "处 130-140，尾部定价中性"
        else:
            skew_txt = "低于 130，尾部对冲需求低迷"
        parts.append(f"SKEW {sk:.0f} {skew_txt}")
    parts.append(
        "遇突发新闻（地缘 / 非农）可能脉冲上行，关注 VIX9D 与 OVX "
        "是否同步抬升确认波动率回升周期。"
    )
    return "。".join(parts)


def vix_history(df: pd.DataFrame, days: int = 6 * 22) -> dict:
    """VIX + SKEW 近 days 个交易日双线（SKEW 对齐到 VIX 日期轴，缺失补 null）。"""
    vix = df["VIX"].dropna().tail(days)
    skew = df["SKEW"].reindex(vix.index)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in vix.index],
        "vix": [round(float(v), 2) for v in vix],
        "skew": [round(float(v), 2) if pd.notna(v) else None for v in skew],
    }


def vix_skew_scatter(df: pd.DataFrame, days: int = 3 * 22) -> list[list]:
    """VIX×SKEW 散点（近 days 个交易日，仅取两序列同时在场）。"""
    pair = df[["VIX", "SKEW"]].dropna().tail(days)
    return [
        [round(float(r["VIX"]), 2), round(float(r["SKEW"]), 1), d.strftime("%Y-%m-%d")]
        for d, r in pair.iterrows()
    ]


def recent_rows(df: pd.DataFrame, n: int = 20) -> list[dict]:
    """近期数据最近 n 条（倒序，最新在前）。"""
    cols = ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y", "SKEW", "OVX"]
    rows = []
    for d, r in df.tail(n).iloc[::-1].iterrows():
        row = {"date": d.strftime("%Y-%m-%d")}
        for c in cols:
            v = r[c]
            row[c] = round(float(v), 2) if pd.notna(v) else None
        rows.append(row)
    return rows


def _llm_generate() -> dict | None:
    """LLM 生成入口（预留）。

    接入方式：在此调用 LLM，传入 `generate_volatility_analysis()` 的结构化数据
    作为上下文，返回同构 dict（signals / vix / term_structure / 图表序列），并置
    `_LLM_ENABLED = True`。未接入时返回 None，调用方自动回落规则引擎。
    """
    return None


_LLM_ENABLED = False


def generate_volatility_analysis() -> dict:
    """波动率研判统一入口：LLM 优先（预留），规则引擎兜底。"""
    if _LLM_ENABLED:
        llm_out = _llm_generate()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    df = _read()
    if df.empty or {"VIX", "SKEW"} - set(df.columns):
        return {
            "error": "data/cboe/volatility.csv 缺 VIX/SKEW 列或为空，"
            "先运行 ./bin/fetch_cboe"
        }
    card = vix_card(df)
    term = term_structure(df)
    hist = vix_history(df)
    return {
        "generator": "rules",
        "as_of": df.index.max().strftime("%Y-%m-%d"),
        "vix": card,
        "term_structure": term,
        "signals": [
            {"title": "VIX 水平", "text": signal_vix_level(card)},
            {"title": "期限结构", "text": signal_term(term)},
            {"title": "展望", "text": signal_outlook(df, card)},
        ],
        "vix_history": hist,
        "vix_skew_scatter": vix_skew_scatter(df),
        "recent": recent_rows(df),
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印渲染结果
    import json

    out = generate_volatility_analysis()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
