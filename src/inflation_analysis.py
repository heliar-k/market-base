"""通胀研判规则引擎 — 通胀专题页数据源（与 rates/credit/volatility 专题同构）。

数据来源（全本地 CSV，确定性规则，无 LLM）：
  data/fred/inflation/inflation.csv             CPI/PCE 及分项 + 盈亏平衡 + 调查预期
  data/fred/producer_prices/producer_prices.csv PPI 四项
  data/shapiro/shapiro.csv                      旧金山联储 PCE 供需分解
  data/sce/sce.csv                              纽约联储 SCE 通胀预期

注意：inflation.csv 是月频（CPI 等）与日频（T5YIE 等）的 outer join，
YoY 必须先 dropna 再按行位移 12 行（月频口径）；日频盈亏平衡变动按 21 个交易日。

LLM 预留同 volatility_analysis：`_llm_generate()` 返回 None = 未启用，
接好后置 `_LLM_ENABLED = True`，调用方（server.py /api/inflation/overview）零改动。

输出结构：
  cards:        CPI / 核心 CPI / 核心 PCE / PPI 四张 YoY 卡片（值 + 环比 pp 变化）
  signals:      三段研判（现状 / 结构驱动 / 预期）
  yoy_history:  CPI / 核心 CPI / 核心 PCE YoY 近 10 年曲线
  components:   CPI 分项 YoY 表（住所/食品/能源/核心商品/核心服务/超级核心）
  expectations: 市场隐含（T5YIE/T10YIE/T5YIFR）+ 调查（MICH/EXPINF/SCE）
  shapiro:      PCE 供需分解（供给/需求/ambiguous 贡献，YoY）
  recent:       最近 12 个月关键 YoY 行
"""

from __future__ import annotations

import pandas as pd

from src.analysis_utils import chg_prev as _chg_prev
from src.analysis_utils import read_csv_or_empty
from src.config import ROOT

FRED_DIR = ROOT / "data" / "fred"

COMPONENT_LABELS = {
    "CPI_SHELTER": "住所",
    "CPI_FOOD": "食品",
    "CPI_ENERGY": "能源",
    "CORE_GOODS": "核心商品",
    "CORE_SERVICES": "核心服务",
    "SUPERCORE_PCE": "超级核心 PCE（除住所服务）",
}

MARKET_EXPECT_LABELS = {
    "T5YIE": "5Y 盈亏平衡通胀率",
    "T10YIE": "10Y 盈亏平衡通胀率",
    "T5YIFR": "5y5y 远期通胀预期",
}

SURVEY_EXPECT_LABELS = {
    "MICH": "密歇根大学 1Y 通胀预期",
    "EXPINF_1Y": "克利夫兰联储 1Y 预期通胀",
}


def _read(name: str) -> pd.DataFrame:
    return read_csv_or_empty(FRED_DIR / name / f"{name}.csv")


def _yoy_series(s: pd.Series, months: int = 12) -> pd.Series:
    """月频指数 → YoY %（先 dropna 再位移，跨频率 outer join 下安全）。"""
    s = s.dropna()
    return ((s / s.shift(months) - 1) * 100).dropna()


def _pts_chg(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 行前的变化（点数/pp）。"""
    pair = _chg_prev(s, n)
    return None if pair is None else round(pair[0] - pair[1], 2)


def _card(s: pd.Series) -> dict:
    """YoY 卡片：最新值 / 较上月变化(pp) / 数据日期。"""
    yoy = _yoy_series(s)
    if yoy.empty:
        return {}
    out = {
        "value": round(float(yoy.iloc[-1]), 2),
        "as_of": yoy.index[-1].strftime("%Y-%m-%d"),
    }
    out["chg_1m"] = (
        round(float(yoy.iloc[-1] - yoy.iloc[-2]), 2) if len(yoy) > 1 else None
    )
    return out


def _dir_label(chg: float | None) -> str:
    if chg is None:
        return "持平"
    if chg > 0.05:
        return f"回升 {chg:+.2f}pp"
    if chg < -0.05:
        return f"回落 {chg:+.2f}pp"
    return "基本持平"


def signal_level(cards: dict) -> str:
    """信号一：现状（三口径 YoY 水平 + 方向 + 距 2% 目标缺口）。"""
    cpi, core_cpi, core_pce = cards["cpi"], cards["core_cpi"], cards["core_pce"]
    text = (
        f"CPI 同比 {cpi['value']}%（较上月{_dir_label(cpi['chg_1m'])}），"
        f"核心 CPI {core_cpi['value']}%，核心 PCE {core_pce['value']}%"
        f"（{core_pce['as_of']}）。"
    )
    gap = round(core_pce["value"] - 2.0, 2)
    if gap > 0.5:
        text += (
            f"核心 PCE 高出美联储 2% 目标 {gap}pp，去通胀尚未完成，"
            "政策利率不具备快速宽松空间。"
        )
    elif gap > 0:
        text += f"核心 PCE 仅高出 2% 目标 {gap}pp，去通胀进入最后一段。"
    else:
        text += "核心 PCE 已回到 2% 目标下方，通胀约束基本解除。"
    if cpi["chg_1m"] is not None and cpi["chg_1m"] > 0.2:
        text += "单月回升幅度偏大，需确认是否为能源/基数效应的一次性扰动。"
    return text


def signal_drivers(comp: dict, shapiro: dict) -> str:
    """信号二：结构驱动（分项 + Shapiro 供需分解）。"""
    v = lambda k: comp.get(k, {}).get("value", "—")  # noqa: E731
    text = (
        f"分项看：住所 {v('CPI_SHELTER')}%，核心服务 {v('CORE_SERVICES')}%，"
        f"超级核心 PCE {v('SUPERCORE_PCE')}%，核心商品 {v('CORE_GOODS')}%，"
        f"能源 {v('CPI_ENERGY')}%。"
    )
    goods = comp.get("CORE_GOODS", {}).get("value")
    if isinstance(goods, float) and goods < 0:
        text += "核心商品处于通缩，对 headline 形成拖累；"
    shelter = comp.get("CPI_SHELTER", {})
    if shelter.get("chg_1m") is not None and shelter["chg_1m"] < 0:
        text += "住所通胀继续降温，滞后租金口径仍在向市场租金收敛；"
    core = shapiro.get("core") or {}
    if core.get("supply") is not None and core.get("demand") is not None:
        driver = "需求" if core["demand"] > core["supply"] else "供给"
        text += (
            f"Shapiro 分解（核心 PCE YoY，{shapiro['as_of']}）："
            f"供给贡献 {core['supply']}pp、需求贡献 {core['demand']}pp，"
            f"当前通胀以{driver}驱动为主——"
            + (
                "需求驱动意味着货币政策收紧仍是对症工具。"
                if driver == "需求"
                else "供给驱动对利率不敏感，紧缩的边际效用最弱。"
            )
        )
    return text


def signal_expectations(market: list, survey: list) -> str:
    """信号三：通胀预期（市场隐含 + 调查，锚定判断）。"""
    m = {r["key"]: r for r in market}
    s = {r["key"]: r for r in survey}
    t5, t5y5y = m.get("T5YIE", {}).get("value"), m.get("T5YIFR", {}).get("value")
    mich = s.get("MICH", {}).get("value")
    sce1 = s.get("SCE_INFL_1Y_MEDIAN", {}).get("value")
    text = f"市场隐含：5Y 盈亏平衡 {t5}%、5y5y 远期 {t5y5y}%"
    if isinstance(t5y5y, float):
        anchored = 1.8 <= t5y5y <= 2.6
        text += (
            "，长期预期仍锚定在 2% 附近"
            if anchored
            else "，长期预期偏离 2% 区间，需警惕失锚"
        )
    text += "。"
    text += f"调查端：密歇根 1Y {mich}%、纽约联储 SCE 1Y {sce1}%。"
    if isinstance(mich, float) and isinstance(t5, float) and mich > t5 + 1.5:
        text += (
            "调查预期显著高于市场隐含，居民体感通胀偏热，"
            "关注其向薪资谈判的传导；市场端未跟进前不必过度定价。"
        )
    else:
        text += "市场与调查预期大体一致，预期端不构成额外风险。"
    return text


def yoy_history(df: pd.DataFrame, months: int = 120) -> dict:
    """CPI / 核心 CPI / 核心 PCE YoY 近 months 个月（对齐 CPI 日期轴）。"""
    cpi = _yoy_series(df["CPI"]).tail(months)
    core = _yoy_series(df["CORE_CPI"]).reindex(cpi.index)
    pce = _yoy_series(df["CORE_PCE"]).reindex(cpi.index)
    r = lambda v: round(float(v), 2) if pd.notna(v) else None  # noqa: E731
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in cpi.index],
        "cpi": [r(v) for v in cpi],
        "core_cpi": [r(v) for v in core],
        "core_pce": [r(v) for v in pce],
    }


def market_expectations(df: pd.DataFrame) -> list[dict]:
    """市场隐含预期（日频，变动按 21 个交易日）。"""
    rows = []
    for key, name in MARKET_EXPECT_LABELS.items():
        if key not in df:
            continue
        s = df[key].dropna()
        if s.empty:
            continue
        rows.append(
            {
                "key": key,
                "name": name,
                "value": round(float(s.iloc[-1]), 2),
                "chg_1m": _pts_chg(s, 21),
                "as_of": s.index[-1].strftime("%Y-%m-%d"),
            }
        )
    return rows


def survey_expectations(df: pd.DataFrame, sce: pd.DataFrame) -> list[dict]:
    """调查端预期（月频，变动按上月，pp）。"""
    rows = []
    for key, name in SURVEY_EXPECT_LABELS.items():
        if key not in df:
            continue
        s = df[key].dropna()
        if s.empty:
            continue
        rows.append(
            {
                "key": key,
                "name": name,
                "value": round(float(s.iloc[-1]), 2),
                "chg_1m": _pts_chg(s, 1),
                "as_of": s.index[-1].strftime("%Y-%m-%d"),
            }
        )
    sce_labels = {
        "SCE_INFL_1Y_MEDIAN": "纽约联储 SCE 1Y 中位数",
        "SCE_INFL_3Y_MEDIAN": "纽约联储 SCE 3Y 中位数",
        "SCE_INFL_5Y_MEDIAN": "纽约联储 SCE 5Y 中位数",
    }
    for key, name in sce_labels.items():
        if sce.empty or key not in sce:
            continue
        s = sce[key].dropna()
        if s.empty:
            continue
        rows.append(
            {
                "key": key,
                "name": name,
                "value": round(float(s.iloc[-1]), 2),
                "chg_1m": _pts_chg(s, 1),
                "as_of": s.index[-1].strftime("%Y-%m-%d"),
            }
        )
    return rows


def shapiro_block(sh: pd.DataFrame) -> dict:
    """Shapiro 供需分解最新一行（YoY 口径，贡献 pp）。"""
    keys = {
        "headline": (
            "SHAPIRO_HEADLINE_YOY_SUPPLY",
            "SHAPIRO_HEADLINE_YOY_DEMAND",
            "SHAPIRO_HEADLINE_YOY_AMBIG",
        ),
        "core": (
            "SHAPIRO_CORE_YOY_SUPPLY",
            "SHAPIRO_CORE_YOY_DEMAND",
            "SHAPIRO_CORE_YOY_AMBIG",
        ),
    }
    out: dict = {}
    for scope, (sup, dem, amb) in keys.items():
        if sh.empty or sup not in sh:
            continue
        s = sh[[sup, dem, amb]].dropna()
        if s.empty:
            continue
        last = s.iloc[-1]
        out.setdefault("as_of", s.index[-1].strftime("%Y-%m-%d"))
        out[scope] = {
            "supply": round(float(last[sup]), 2),
            "demand": round(float(last[dem]), 2),
            "ambig": round(float(last[amb]), 2),
        }
    return out


def recent_rows(df: pd.DataFrame, ppi: pd.DataFrame, n: int = 12) -> list[dict]:
    """最近 n 个月关键 YoY（倒序，最新在前）。"""
    yoy = {
        "cpi_yoy": _yoy_series(df["CPI"]),
        "core_cpi_yoy": _yoy_series(df["CORE_CPI"]),
        "core_pce_yoy": _yoy_series(df["CORE_PCE"]),
    }
    if not ppi.empty and "PPI_FD" in ppi:
        yoy["ppi_yoy"] = _yoy_series(ppi["PPI_FD"])
    base = yoy["cpi_yoy"].tail(n).iloc[::-1]
    rows = []
    for d in base.index:
        row = {"date": d.strftime("%Y-%m-%d")}
        for k, s in yoy.items():
            v = s.get(d)
            row[k] = round(float(v), 2) if pd.notna(v) else None
        rows.append(row)
    return rows


def _llm_generate() -> dict | None:
    """LLM 生成入口（预留，未启用返回 None，调用方回落规则引擎）。"""
    return None


_LLM_ENABLED = False


def generate_inflation_overview() -> dict:
    """通胀专题总览统一入口：LLM 优先（预留），规则引擎兜底。"""
    if _LLM_ENABLED:
        llm_out = _llm_generate()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    df = _read("inflation")
    if df.empty or "CPI" not in df.columns:
        return {
            "error": "data/fred/inflation/inflation.csv 缺失或为空，"
            "先运行 ./bin/fetch_fred"
        }
    ppi = _read("producer_prices")
    sce = read_csv_or_empty(ROOT / "data" / "sce" / "sce.csv")
    shapiro = read_csv_or_empty(ROOT / "data" / "shapiro" / "shapiro.csv")

    cards = {
        "cpi": _card(df["CPI"]),
        "core_cpi": _card(df["CORE_CPI"]),
        "core_pce": _card(df["CORE_PCE"]),
        "ppi": _card(ppi["PPI_FD"]) if not ppi.empty and "PPI_FD" in ppi else {},
    }
    if not cards["cpi"]:
        return {"error": "inflation.csv 缺 CPI 有效数据（YoY 需 13 个月以上历史）"}
    comp = {
        k: {**_card(df[k]), "name": name}
        for k, name in COMPONENT_LABELS.items()
        if k in df
    }
    comp = {k: v for k, v in comp.items() if v.get("value") is not None}
    shapiro_out = shapiro_block(shapiro)
    market = market_expectations(df)
    survey = survey_expectations(df, sce)

    return {
        "generator": "rules",
        "as_of": cards["cpi"]["as_of"],
        "cards": cards,
        "signals": [
            {"title": "通胀现状", "text": signal_level(cards)},
            {"title": "结构驱动", "text": signal_drivers(comp, shapiro_out)},
            {"title": "通胀预期", "text": signal_expectations(market, survey)},
        ],
        "yoy_history": yoy_history(df),
        "components": [
            {
                "name": v["name"],
                "yoy": v["value"],
                "chg_1m": v["chg_1m"],
                "as_of": v["as_of"],
            }
            for v in comp.values()
        ],
        "expectations": {"market": market, "survey": survey},
        "shapiro": shapiro_out,
        "recent": recent_rows(df, ppi),
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印摘要
    import json

    out = generate_inflation_overview()
    assert "cards" in out, out.get("error")
    assert out["cards"]["core_pce"]["value"] is not None
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
