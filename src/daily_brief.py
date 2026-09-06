"""今日研判数据引擎 — 复刻 timsun.net 首页「今日宏观决策台」结构。

只读本地 CSV（与 *_analysis 同构），产出单一 JSON：
  indicators: 跨资产变化表 10 行（最新值 + 5/20 观测变化 + 迷你走势 + 截至 + 时效）
  scenarios:  同一组事实的三种可能解释（规则匹配，支持=规则命中数，不是概率）
  alerts:     跨资产结构报警（复用 assets_analysis.overview 的 alerts）

LLM 预留：_llm_generate() 接好后返回同构 dict，未接入回落规则引擎。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis_utils import read_csv_or_empty

ROOT = Path(__file__).resolve().parent.parent

# 指标窗口（观测间隔数，timsun 同款：情景 5 个 / 表格 5+20 个）
SCEN_WINDOW = 5
TABLE_WINDOWS = (5, 20)
SPARK_N = 20

# 跨资产观察表行（key → 名称/一句话说明/单位）——timsun 首页 10 行同款
INDICATORS: list[tuple[str, str, str, str]] = [
    ("SPX", "标普500", "股票风险偏好的价格表现；需与信用和波动率交叉验证。", "px"),
    ("DXY", "美元指数", "美元相对一篮子货币的强弱；不能单独证明全球美元短缺。", "px"),
    ("BTC", "比特币", "全天候风险资产；同时受杠杆、ETF 与加密原生事件影响。", "px"),
    (
        "WTI",
        "WTI 原油",
        "近月原油期货；供给与需求都能推动价格，换月也会影响序列。",
        "px",
    ),
    ("Gold", "黄金", "避险与实际利率的镜像；需结合美元方向解读。", "px"),
    ("Y10", "10Y 国债收益率", "名义利率；需拆分实际利率、通胀补偿与期限溢价。", "bp"),
    ("VIX", "VIX", "标普期权隐含的未来约 30 天波动预期，不是涨跌方向预测。", "pt"),
    (
        "HY_OAS",
        "高收益债 OAS",
        "高收益债相对国债的利差；扩大通常意味着信用风险补偿上升。",
        "bp",
    ),
    ("RRP", "隔夜逆回购余额", "隔夜逆回购余额；下降不等于同额资金直接流入股票。", "bn"),
    ("TGA", "TGA 余额", "财政部现金余额；其影响需结合发债、支出及资金来源。", "bn"),
    (
        "NET_LIQ",
        "净流动性",
        "Fed 总资产 − RRP − TGA 的资产负债表代理量，不等于可投资现金。",
        "bn",
    ),
]


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


# ── 跨资产变化表 ────────────────────────────────────────────────────────────


def _chg(s: pd.Series, n: int, mode: str) -> float | None:
    """最新 vs n 个观测前的变化：px → %，bp/pt/bn → 差值。"""
    if len(s) < n + 1:
        return None
    cur, past = float(s.iloc[-1]), float(s.iloc[-1 - n])
    if past == 0:
        return None
    return round((cur / past - 1) * 100, 2) if mode == "px" else round(cur - past, 2)


def _spark(s: pd.Series, n: int = SPARK_N) -> list[list]:
    """近 n 个观测的 [date, value]（迷你趋势，独立纵轴）。"""
    s = s.tail(n)
    return [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in s.items()]


def _indicators(series: dict[str, pd.Series]) -> dict:
    rows = []
    for key, name, note, unit in INDICATORS:
        s = series.get(key)
        if s is None or s.empty:
            rows.append({"key": key, "name": name, "note": note, "unit": unit})
            continue
        row = {
            "key": key,
            "name": name,
            "note": note,
            "unit": unit,
            "last": round(float(s.iloc[-1]), 2),
            "as_of": s.index[-1].strftime("%Y-%m-%d"),
            "chg": {f"d{n}": _chg(s, n, unit) for n in TABLE_WINDOWS},
            "spark": _spark(s),
        }
        rows.append(row)
    dates = [r["as_of"] for r in rows if r.get("as_of")]
    return {"as_of": max(dates) if dates else None, "rows": rows}


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
    spx, hy, vix, wti, y10 = (
        series.get(k) for k in ("SPX", "HY_OAS", "VIX", "WTI", "Y10")
    )
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
    }
