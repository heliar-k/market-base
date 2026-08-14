"""就业研判规则引擎 — 就业专题页数据源（与 inflation/treasury 专题同构）。

数据来源（全本地 CSV，确定性规则，无 LLM）：
  data/fred/labor/labor.csv                UNRATE / PAYEMS（月频）+ ICSA（周频）
  data/fred/labor_market/labor_market.csv  JOLTS 空缺/离职（月频）
                                                + UNEMPLOY + ECI（季频）

口径：NFP = PAYEMS 一阶差分（千人）；V/U = JOLTS_OPEN / UNEMPLOY；
Sahm 规则 = 失业率 3M 均值 − 近 12M 3M 均值最小值，≥0.5 触发衰退信号；
ECI 薪资 = 季频指数 YoY（位移 4 行）。

输出结构：
  cards:       失业率 / 新增非农 / 初请 / JOLTS 空缺（含 V/U）
  signals:     三段研判（现状 / 结构 / 展望）
  nfp_history: NFP 月度新增 bar + 失业率 line（近 3 年，双轴）
  indicators:  7 指标明细表
"""

from __future__ import annotations

import pandas as pd

from src.analysis_utils import chg_prev as _chg_prev
from src.analysis_utils import read_csv_or_empty
from src.config import ROOT, config

FRED_DIR = ROOT / "data" / "fred"

INDICATOR_LABELS = {
    "UNRATE": ("失业率", "%"),
    "PAYEMS": ("非农就业总人数", "K"),
    "ICSA": ("初请失业金人数", "K"),
    "JOLTS_OPEN": ("JOLTS 职位空缺", "K"),
    "JOLTS_QUITS": ("JOLTS 主动离职", "K"),
    "UNEMPLOY": ("失业人数", "K"),
    "ECI_WAGES": ("ECI 薪资指数", "指数"),
}


def _read(name: str) -> pd.DataFrame:
    return read_csv_or_empty(FRED_DIR / name / f"{name}.csv")


def _latest(s: pd.Series) -> tuple[float, pd.Timestamp] | None:
    s = s.dropna()
    return None if s.empty else (float(s.iloc[-1]), s.index[-1])


def sahm_rule(unrate: pd.Series) -> dict:
    """Sahm 规则：3M 均值 − 近 12M 最低 3M 均值；≥0.5 = 衰退信号。"""
    u = unrate.dropna()
    if len(u) < 14:
        return {}
    avg3 = u.rolling(3).mean().dropna()
    cur = float(avg3.iloc[-1])
    low = float(avg3.tail(12).min())
    return {
        "value": round(cur - low, 2),
        "avg3": round(cur, 2),
        "low_12m": round(low, 2),
    }


def vu_ratio(lm: pd.DataFrame) -> pd.Series:
    """职位空缺 / 失业人数（月度，对齐后 dropna）。"""
    if lm.empty or {"JOLTS_OPEN", "UNEMPLOY"} - set(lm.columns):
        return pd.Series(dtype=float)
    pair = lm[["JOLTS_OPEN", "UNEMPLOY"]].dropna()
    return (pair["JOLTS_OPEN"] / pair["UNEMPLOY"]).dropna()


def signal_current(cards: dict, sahm: dict) -> str:
    """信号一：现状（失业率 + Sahm + NFP + 初请）。"""
    u, nfp, icsa = cards["unrate"], cards["nfp"], cards["icsa"]
    text = f"失业率 {u['value']}%"
    if u.get("chg_3m") is not None:
        text += f"（近 3 个月 {u['chg_3m']:+.1f}pp）"
    text += (
        f"；近月非农新增 {nfp['value']:.0f}K"
        f"（3M 均值 {nfp['avg_3m']:.0f}K）；初请 {icsa['value']:.0f}K"
        f"（4 周均值 {icsa['avg_4w']:.0f}K）。"
    )
    if sahm.get("value") is not None:
        text += f"Sahm 指标 {sahm['value']:.2f}"
        if sahm["value"] >= 0.5:
            text += "，已触发 0.5 衰退阈值——历史上该信号触发后失业率均持续上行。"
        elif sahm["value"] >= 0.3:
            text += "，接近 0.5 衰退阈值，就业降温斜率需密切跟踪。"
        else:
            text += "，距 0.5 衰退阈值仍有缓冲。"
    if nfp["avg_3m"] < 100:
        text += "非农 3M 均值低于 100K，需求端明显减速。"
    return text


def signal_structure(
    cards: dict, vu: pd.Series, eci_yoy: float | None, quits_chg_6m: float | None
) -> str:
    """信号二：结构（V/U + 离职 + 薪资）。"""
    j = cards["jolts"]
    text = f"职位空缺 {j['value']:.2f}M，V/U 比 {j.get('vu')}"
    v = vu.dropna().iloc[-1] if not vu.empty else None
    if v is not None:
        if v >= 1.0:
            text += "——空缺仍多于失业者，劳动力市场偏紧但正向平衡回归；"
        else:
            text += "——空缺已少于失业者，市场转入供大于求，议价权回到雇主侧；"
    if quits_chg_6m is not None:
        if abs(quits_chg_6m) < 10:
            text += "主动离职基本持平；"
        else:
            text += (
                f"主动离职 6 个月{'增加' if quits_chg_6m >= 0 else '减少'} "
                f"{abs(quits_chg_6m):.0f}K"
            )
            if quits_chg_6m < 0:
                text += "（员工跳槽信心走弱，领先于薪资降温）"
            text += "；"
    if eci_yoy is not None:
        text += f"ECI 薪资同比 {eci_yoy:.2f}%"
        text += (
            "，薪资增速仍高于与 2% 通胀相容的 ~3.5%。"
            if eci_yoy > 3.5
            else "，薪资增速已与 2% 通胀目标大体相容。"
        )
    return text


def signal_outlook(cards: dict, sahm: dict) -> str:
    """信号三：展望（初请趋势 + NFP 减速 + Sahm 合成）。"""
    icsa, nfp = cards["icsa"], cards["nfp"]
    weakening = []
    if icsa.get("chg_4w") is not None and icsa["chg_4w"] > 10:
        weakening.append("初请 4 周均值抬升")
    if nfp.get("avg_3m") is not None and nfp["avg_3m"] < 100:
        weakening.append("非农均值跌破 100K")
    if sahm.get("value") is not None and sahm["value"] >= 0.3:
        weakening.append("Sahm 指标走高")
    if weakening:
        return (
            "降温信号：" + "、".join(weakening) + "。"
            "就业是美联储双重使命的短板侧，若降温信号持续叠加，"
            "政策反应函数将偏向宽松；关注下周初请与下月非农是否确认。"
        )
    return (
        "初请低位、非农稳健、Sahm 未触发，就业市场尚无系统性走弱证据；"
        "渐进降温仍是基准情形，关注 JOLTS 与离职率的领先信号。"
    )


def nfp_history(labor: pd.DataFrame, months: int = 36) -> dict:
    """NFP 月度新增（K）+ 失业率（%）近 months 个月。"""
    nfp = labor["PAYEMS"].dropna().diff().dropna().tail(months)
    u = labor["UNRATE"].reindex(nfp.index)
    r = lambda v: round(float(v), 1) if pd.notna(v) else None  # noqa: E731
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in nfp.index],
        "nfp": [r(v) for v in nfp],
        "unrate": [r(v) for v in u],
    }


def _release_dates() -> dict[str, str]:
    """FRED 系列 → last_updated（最近发布/修订日，data/fred/_release_dates.csv）。"""
    p = FRED_DIR / "_release_dates.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str)
    return dict(zip(df.series_id, df.last_updated))


def indicators_table(labor: pd.DataFrame, lm: pd.DataFrame) -> list[dict]:
    """7 指标最新值 + 较上期变化（ICSA 按 4 周前，ECI 按上季）+ 发布时间。"""
    src = {
        **{k: labor[k] for k in ("UNRATE", "PAYEMS", "ICSA") if k in labor},
        **{
            k: lm[k]
            for k in ("JOLTS_OPEN", "JOLTS_QUITS", "UNEMPLOY", "ECI_WAGES")
            if k in lm
        },
    }
    # 指标名 → FRED 系列 ID（查发布日用）
    sid = {**config.fred_series["labor"], **config.fred_series["labor_market"]}
    rel = _release_dates()
    lag = {"ICSA": 4, "ECI_WAGES": 1}
    rows = []
    for key, (name, unit) in INDICATOR_LABELS.items():
        s = src.get(key)
        if s is None:
            continue
        pair = _latest(s)
        if pair is None:
            continue
        v, dt = pair
        prev = _chg_prev(s, lag.get(key, 1))
        rel_date = (rel.get(sid.get(key, "")) or "")[:10]
        rows.append(
            {
                "name": name,
                "value": round(v, 2),
                "unit": unit,
                "chg": round(prev[0] - prev[1], 2) if prev else None,
                "as_of": dt.strftime("%Y-%m-%d"),
                "released": rel_date,
            }
        )
    return rows


def generate_labor_overview() -> dict:
    """就业专题总览统一入口（规则引擎，LLM 预留同 inflation_analysis）。"""
    labor = _read("labor")
    if labor.empty or {"UNRATE", "PAYEMS", "ICSA"} - set(labor.columns):
        return {
            "error": "data/fred/labor/labor.csv 缺失或缺列，先运行 ./bin/fetch_fred"
        }
    lm = _read("labor_market")

    unrate = labor["UNRATE"].dropna()
    nfp = labor["PAYEMS"].dropna().diff().dropna()
    icsa = labor["ICSA"].dropna()
    jolts = lm["JOLTS_OPEN"].dropna() if "JOLTS_OPEN" in lm else pd.Series(dtype=float)
    vu = vu_ratio(lm)
    sahm = sahm_rule(labor["UNRATE"])

    u_pair, nfp_pair, icsa_pair = _latest(unrate), _latest(nfp), _latest(icsa)
    if not all([u_pair, nfp_pair, icsa_pair]):
        return {"error": "labor.csv 有效数据不足"}

    eci = lm["ECI_WAGES"].dropna() if "ECI_WAGES" in lm else pd.Series(dtype=float)
    eci_pair = _chg_prev(eci, 4)
    quits_pair = (
        _chg_prev(lm["JOLTS_QUITS"].dropna(), 6) if "JOLTS_QUITS" in lm else None
    )
    j_pair = _latest(jolts)

    cards = {
        "unrate": {
            "value": round(u_pair[0], 1),
            "chg_3m": round(unrate.iloc[-1] - unrate.iloc[-4], 2)
            if len(unrate) > 3
            else None,
            "sahm": sahm.get("value"),
            "as_of": u_pair[1].strftime("%Y-%m-%d"),
        },
        "nfp": {
            "value": round(nfp_pair[0], 0),
            "avg_3m": round(float(nfp.tail(3).mean()), 0),
            "as_of": nfp_pair[1].strftime("%Y-%m-%d"),
        },
        "icsa": {
            "value": round(icsa_pair[0] / 1000, 0),
            "avg_4w": round(float(icsa.tail(4).mean()) / 1000, 0),
            "chg_4w": round(
                (icsa.tail(4).mean() - icsa.tail(8).head(4).mean()) / 1000, 0
            )
            if len(icsa) >= 8
            else None,
            "as_of": icsa_pair[1].strftime("%Y-%m-%d"),
        },
        "jolts": {
            "value": round(j_pair[0] / 1000, 2) if j_pair else None,
            "vu": round(float(vu.iloc[-1]), 2) if not vu.empty else None,
            "as_of": j_pair[1].strftime("%Y-%m-%d") if j_pair else None,
        },
    }

    eci_yoy = round((eci_pair[0] / eci_pair[1] - 1) * 100, 2) if eci_pair else None
    quits_chg = round(quits_pair[0] - quits_pair[1], 0) if quits_pair else None

    return {
        "generator": "rules",
        "as_of": cards["unrate"]["as_of"],
        "cards": cards,
        "signals": [
            {"title": "就业现状", "text": signal_current(cards, sahm)},
            {"title": "结构", "text": signal_structure(cards, vu, eci_yoy, quits_chg)},
            {"title": "展望", "text": signal_outlook(cards, sahm)},
        ],
        "nfp_history": nfp_history(labor),
        "indicators": indicators_table(labor, lm),
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印摘要
    import json

    out = generate_labor_overview()
    assert "cards" in out, out.get("error")
    assert out["cards"]["unrate"]["value"] is not None
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:3000])
