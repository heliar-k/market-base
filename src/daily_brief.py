"""今日研判数据引擎 — 头条区数据源（时效分组 + 三情景规则匹配 + 跨资产报警 + Polymarket 锚点）。

只读本地 CSV（与 *_analysis 同构），产出单一 JSON：
  indicators: 各分组最新观测日（页头多源时效分段用）
  scenarios:  同一组事实的三种可能解释（规则匹配，支持=规则命中数，不是概率）
  alerts:     跨资产结构报警（复用 assets_analysis.overview 的 alerts）
  polymarket: 衰退概率与地缘热点

LLM 预留：_llm_generate() 接好后返回同构 dict，未接入回落规则引擎。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis_utils import read_csv_or_empty

ROOT = Path(__file__).resolve().parent.parent

SCEN_WINDOW = 5

# 数据截至分组（页头多源分段用）
AS_OF_GROUPS = {
    "资产": ("SPX", "DXY", "BTC", "WTI", "Gold"),
    "利率/利差": ("Y10", "VIX", "HY_OAS"),
    "流动性": ("RRP", "TGA", "NET_LIQ"),
}


def _llm_generate() -> dict | None:
    """LLM 生成接入点（各专题分析同款）。未接入返回 None → 回落规则引擎。"""
    return None


# ── 数据层（只读）───────────────────────────────────────────────────────────


def _series() -> dict[str, pd.Series]:
    """11 个指标的日度序列（各自 dropna）。"""
    rates = read_csv_or_empty(ROOT / "data/fred/rates/rates.csv")
    vol = read_csv_or_empty(ROOT / "data/fred/volatility/volatility.csv")
    liq = read_csv_or_empty(ROOT / "data/fred/liquidity/liquidity.csv")
    prices = read_csv_or_empty(ROOT / "data/yfinance/asset_prices.csv")

    out: dict[str, pd.Series] = {}
    for k in ("SPX", "DXY", "BTC", "WTI", "Gold"):
        if k in prices.columns:
            out[k] = prices[k].dropna()
    if "DGS10" in rates.columns:
        out["Y10"] = rates["DGS10"].dropna()
    if "VIX" in vol.columns:
        out["VIX"] = vol["VIX"].dropna()
    if "HY_OAS" in vol.columns:
        out["HY_OAS"] = (vol["HY_OAS"] * 100).dropna()  # % → bp
    if "RRPONTSYD" in liq.columns:
        out["RRP"] = liq["RRPONTSYD"].dropna()
    if "WTREGEN" in liq.columns:
        out["TGA"] = liq["WTREGEN"].dropna()
    if {"WALCL", "RRPONTSYD", "WTREGEN"} <= set(liq.columns):
        out["NET_LIQ"] = (liq["WALCL"] - liq["RRPONTSYD"] - liq["WTREGEN"]).dropna()
    return out


def _indicators(series: dict[str, pd.Series]) -> dict:
    by_key = {
        k: s.index[-1].strftime("%Y-%m-%d")
        for k, s in series.items()
        if s is not None and not s.empty
    }
    groups = {}
    for g, keys in AS_OF_GROUPS.items():
        ds = [by_key[k] for k in keys if by_key.get(k)]
        if ds:
            groups[g] = max(ds)
    return {"as_of": max(by_key.values()) if by_key else None, "groups": groups}


# ── 三种可能解释（规则匹配）─────────────────────────────────────────────────


def _dir(s: pd.Series | None, n: int = SCEN_WINDOW) -> int | None:
    """近 n 个观测的方向：+1 上行 / -1 下行 / 0 持平 / None 数据不足。"""
    if s is None or len(s) < n + 1:
        return None
    cur, past = float(s.iloc[-1]), float(s.iloc[-1 - n])
    if abs(cur - past) < 1e-9:
        return 0
    return 1 if cur > past else -1


def _window(series: dict[str, pd.Series], keys: list[str]) -> tuple[str, str] | None:
    """情景窗口 = 所需序列共同覆盖的 [起点, 终点]（起点取最晚，终点取最早）。"""
    ok = [k for k in keys if series.get(k) is not None and len(series[k]) > SCEN_WINDOW]
    if not ok:
        return None
    start = max(series[k].index[-1 - SCEN_WINDOW] for k in ok)
    end = min(series[k].index[-1] for k in ok)
    if start >= end:
        return None
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _scenarios(series: dict[str, pd.Series]) -> list[dict]:
    spx, hy, vix, wti, y10 = (series.get(k) for k in ("SPX", "HY_OAS", "VIX", "WTI", "Y10"))
    d_spx, d_hy, d_vix, d_wti, d_y10 = (_dir(x) for x in (spx, hy, vix, wti, y10))
    win = lambda keys: _window(series, keys)  # noqa: E731

    def ev(label: str, d: int | None) -> str:
        arrow = {1: "上行", -1: "下行", 0: "持平", None: "数据不足"}[d]
        return f"{label}近 5 个观测{arrow}"

    return [
        {
            "key": "risk-on",
            "title": "风险偏好修复",
            "desc": "股市上涨若伴随信用利差收窄、隐含波动下降，风险承担改善更有广度。",
            "matched": d_spx == 1 and d_hy in (-1, 0) and d_vix in (-1, 0),
            # topics = 情景用到的序列归属的 home-lines 专题键（仪表盘给对应行标主次）
            "topics": ["assets", "credit", "vol"],
            "evidence": [
                ev("标普500", d_spx),
                ev("高收益债 OAS", d_hy),
                ev("VIX", d_vix),
            ],
            "window": win(["SPX", "HY_OAS", "VIX"]),
            "refute": "若股市上涨而信用利差扩大或 VIX 上升，应下调这一解释的权重。",
        },
        {
            "key": "risk-off",
            "title": "风险压力扩散",
            "desc": "股市下跌若同时传导到信用与期权市场，应检查融资及流动性条件。",
            "matched": d_spx == -1 and (d_hy == 1 or d_vix == 1),
            "topics": ["assets", "credit", "vol"],
            "evidence": [
                ev("标普500", d_spx),
                ev("高收益债 OAS", d_hy),
                ev("VIX", d_vix),
            ],
            "window": win(["SPX", "HY_OAS", "VIX"]),
            "refute": "若信用保持稳定、波动率回落，调整可能仍是局部估值或仓位变化。",
        },
        {
            "key": "energy-cost",
            "title": "能源成本压力",
            "desc": "油价涨、股市跌、长端利率升并存，与成本压力相容；仍需事件验证。",
            "matched": d_wti == 1 and d_spx == -1 and d_y10 == 1,
            "topics": ["assets", "rates"],
            "evidence": [
                ev("WTI 原油", d_wti),
                ev("标普500", d_spx),
                ev("10Y 收益率", d_y10),
            ],
            "window": win(["WTI", "SPX", "Y10"]),
            "refute": "油价回落、股票企稳或实际需求改善，都可能改变成本冲击的解释。",
        },
    ]


# ── 报警（复用 assets_analysis 的结构报警对）────────────────────────────────


def _alerts() -> list[dict]:
    try:
        from src.assets_analysis import overview as assets_overview

        return assets_overview().get("alerts") or []
    except Exception:  # 数据缺失不阻断今日研判
        return []


def _polymarket() -> dict | None:
    """预测市场锚点：衰退概率 + 地缘热点主题事件（结论层 chip 素材）。

    衰退事件取 data 分类里标题含 "recession" 的 24h 量最大者，概率取其
    24h 量最大市场（主市场；多档事件不会被任意序带错档）；地缘热点取
    geo 分类前 5。无快照返回 None（前端不渲染）。
    """
    from src.polymarket_analysis import events_matching, snapshot

    snap = snapshot()
    if snap is None:
        return None
    out: dict = {"as_of": snap.get("as_of")}
    rec = events_matching(snap, pattern=r"recession", categories=("data",))
    if rec:
        e = rec[0]
        main = max(
            e.get("markets") or [],
            key=lambda m: m.get("volume24hr") or 0,
            default=None,
        )
        out["recession"] = {
            "title": e["title"],
            "end_date": e.get("end_date"),
            "prob": main.get("prob_yes") if main else None,
        }
    out["geo_hot"] = [
        {
            "title": e["title"],
            "end_date": e.get("end_date"),
            "volume24hr": e.get("volume24hr"),
        }
        for e in events_matching(snap, categories=("geo",), exclude_sports=True)[:5]
    ]
    return out


# ── 统一入口 ────────────────────────────────────────────────────────────────


def generate_daily_brief() -> dict:
    out = _llm_generate()
    if out is not None:
        return out
    series = _series()
    return {
        "generator": "rules",
        "indicators": _indicators(series),
        "scenarios": _scenarios(series),
        "alerts": _alerts(),
        "polymarket": _polymarket(),
    }
