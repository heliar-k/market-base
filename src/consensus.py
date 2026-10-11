"""多源共识层 —— 两级同构状态卡（ADR-0004 / issue #33）。

市场卡（风险轴）: 确认 risk-on / 确认 risk-off / 分歧
宏观卡（条件轴）: 确认宽松 / 确认紧缩 / 分歧

投票规则（两卡共用）:
- 成员内部子信号各自判 on/off/弃权，成员票 = 子信号多数，平票 → 成员弃权
- 参与票 ≥ CONFIRM_RATIO 同向 → 「确认」，否则「分歧」
- 缺数据成员剔除分母，缺席过半 → 「数据不足」
- 票龄 > STALE_BUS_DAYS 个工作日算缺席（周末顺延；节假日使其略宽松，宁漏报不误报）

时效: daily-full 收盘定稿；intraday-refresh 传 --intraday 重算（upsert 不覆盖定稿行）。
留痕: data/consensus/history.csv 每日一行（source=final/intraday）。

用法:
    uv run python -m src.consensus             # 计算两卡 + upsert 历史 + 打印 JSON
    uv run python -m src.consensus --intraday  # 盘中临时值（不覆盖定稿行）
    uv run python -m src.consensus --no-save   # 只算不落盘
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis_utils import read_csv_or_empty
from src.macro import derive_macro

ROOT = Path(__file__).resolve().parent.parent

# ── 规则常量（票面阈值，改动须同步 issue #33 规则表）──
CONFIRM_RATIO = 2 / 3  # 参与票同向占比 ≥ 此值 → 确认
STALE_BUS_DAYS = 2  # 票龄超过 N 个工作日算缺席
SUB_WIN = 21  # 「1 个月变化」的观测窗口（交易日）

# 市场卡阈值
VIX_ON, VIX_OFF = 17.0, 22.0  # VIX < 17 on / > 22 off，中间弃权
ABV_ON, ABV_OFF = 60.0, 40.0  # ABV50 分界
HY_OAS_ON_BP, HY_OAS_OFF_BP = -10.0, 20.0  # HY OAS 1M 变化 bp
SHORT_RATIO_WIN = 5  # 卖空比率均值窗口
SHORT_RATIO_ON_PP, SHORT_RATIO_OFF_PP = -1.5, 1.5  # 5 日均值差（百分点）

# 宏观卡阈值
PROB_CUT_ON, PROB_HIKE_OFF = 0.60, 0.40  # 最近会期降息/加息概率
NET_LIQ_CHG_B = 50.0  # 净流动性 1M 变化（十亿美元）
SOFR_IORB_ON_BP, SOFR_IORB_OFF_BP = -8.0, 0.0  # SOFR−IORB（bp）
SPREAD_2S10S_ON, SPREAD_2S10S_OFF = 30.0, 0.0  # 2s10s 水平（bp）
DFII10_CHG_BP = 15.0  # 10Y TIPS 1M 变化（bp）
TGA_CHG_B = 50.0  # TGA 1M 变化（十亿美元）

# 状态词（CONTEXT.md 词汇表同步）
MKT_ON, MKT_OFF, MKT_DIV = "确认 risk-on", "确认 risk-off", "分歧"
MAC_ON, MAC_OFF, MAC_DIV = "确认宽松", "确认紧缩", "分歧"
STATE_NA = "数据不足"

# 成员 → 专题页跳转（首页卡点击联动）
PAGE_LINKS = {
    "趋势": "/daily/",
    "波动率": "/volatility/vix/",
    "广度": "/assets/equities/",
    "信用": "/credit/",
    "期权结构": "/assets/equities/options/",
    "卖空": "/assets/equities/positioning/",
    "政策路径": "/rates/pricing/",
    "净流动性": "/liquidity/",
    "货币市场": "/liquidity/operations/",
    "曲线": "/rates/yield-curve/",
    "实际利率": "/rates/",
    "财政": "/treasury/",
}


# ── 基础工具 ──


def _col(df: pd.DataFrame, name: str) -> pd.Series | None:
    """取数值列（缺失或全 NaN 返回 None）。"""
    if df.empty or name not in df.columns:
        return None
    s = pd.to_numeric(df[name], errors="coerce").dropna()
    return s if not s.empty else None


def _chg(s: pd.Series, win: int = SUB_WIN) -> float | None:
    """近 win 个观测的变化量（末值 − win 前值）。"""
    if len(s) < win + 1:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - win])


def _age_bus_days(as_of: str, today: date) -> int:
    """票龄：as_of → today 之间的工作日数（周末顺延）。"""
    try:
        d0 = pd.Timestamp(as_of).date()
    except ValueError:
        return 999
    return int(np.busday_count(d0, today))


def _sub(name: str, vote: bool | None, value: float | None, as_of: str | None) -> dict:
    """子信号：vote True=on / False=off / None=弃权。"""
    return {"name": name, "vote": vote, "value": value, "as_of": as_of}


def _member(name: str, subs: list[dict]) -> dict:
    """成员 = 子信号多数；全子信号无数据 → 缺席；平票 → 弃权。"""
    voted = [s for s in subs if s["vote"] is not None]
    has_data = any(s["as_of"] for s in subs)
    if not voted:
        return {
            "name": name,
            "vote": None,
            "present": has_data,
            "subs": subs,
            "as_of": max((s["as_of"] for s in subs if s["as_of"]), default=None),
        }
    n_on = sum(s["vote"] for s in voted)
    n_off = len(voted) - n_on
    vote = "on" if n_on > n_off else ("off" if n_off > n_on else None)  # 平票弃权
    return {
        "name": name,
        "vote": vote,
        "present": True,
        "subs": subs,
        "as_of": max((s["as_of"] for s in subs if s["as_of"]), default=None),
    }


def _apply_staleness(members: list[dict], today: date) -> None:
    """票龄超限的成员就地标记为缺席（vote/present 归零，stale 注记）。"""
    for m in members:
        if m["as_of"] and _age_bus_days(m["as_of"], today) > STALE_BUS_DAYS:
            m["vote"] = None
            m["present"] = False
            m["stale"] = True


def _aggregate(members: list[dict], on_label: str, off_label: str) -> dict:
    """成员票 → 卡状态（≥2/3 同向确认；缺席过半或全弃权 → 数据不足）。"""
    n = len(members)
    present = [m for m in members if m["present"]]
    on = [m for m in members if m["vote"] == "on"]
    off = [m for m in members if m["vote"] == "off"]
    n_votes = len(on) + len(off)
    if len(present) * 2 < n or n_votes == 0:
        state = STATE_NA
    elif max(len(on), len(off)) / n_votes >= CONFIRM_RATIO:
        state = on_label if len(on) > len(off) else off_label
    else:
        state = "分歧"
    return {
        "state": state,
        "members": members,
        "n_members": n,
        "n_present": len(present),
        "n_on": len(on),
        "n_off": len(off),
        "n_abstain": len(present) - n_votes,
    }


# ── 市场卡成员 ──


def _m_trend(today: date) -> dict:
    """趋势：SPX 收盘 vs MA60 + MA20 vs MA60（双确认）。"""
    df = read_csv_or_empty(ROOT / "data/indices/SPX.csv")
    close = _col(df, "close")
    if close is None or len(close) < 60:
        return _member("趋势", [_sub("收盘 vs MA60", None, None, None)])
    ma20, ma60 = close.rolling(20).mean(), close.rolling(60).mean()
    c, m20, m60 = float(close.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1])
    last = close.index[-1]
    as_of = str(last.date()) if hasattr(last, "date") else str(last)
    return _member(
        "趋势",
        [
            _sub("收盘 vs MA60", c > m60, round(c - m60, 1), as_of),
            _sub("MA20 vs MA60", m20 > m60, round(m20 - m60, 1), as_of),
        ],
    )


def _m_vol(today: date) -> dict:
    """波动率：VIX 水平区间 + VIX 期限斜率（contango/backwardation）。"""
    df = read_csv_or_empty(ROOT / "data/cboe/volatility.csv")
    vix, vix3m = _col(df, "VIX"), _col(df, "VIX3M")
    subs = []
    if vix is not None:
        v = float(vix.iloc[-1])
        as_of = str(vix.index[-1].date())
        vote = True if v < VIX_ON else (False if v > VIX_OFF else None)
        subs.append(_sub("VIX 水平", vote, v, as_of))
    if vix is not None and vix3m is not None:
        slope = float(vix.iloc[-1] - vix3m.iloc[-1])
        as_of = str(vix.index[-1].date())
        subs.append(_sub("VIX 期限斜率", slope < 0, round(slope, 2), as_of))  # contango=on
    return _member("波动率", subs)


def _m_breadth(today: date) -> dict:
    """广度：ABV50 水平分档。"""
    df = read_csv_or_empty(ROOT / "data/breadth/abv.csv")
    s = _col(df, "ABV50")
    if s is None:
        return _member("广度", [_sub("ABV50", None, None, None)])
    v = float(s.iloc[-1])
    vote = True if v >= ABV_ON else (False if v <= ABV_OFF else None)
    return _member("广度", [_sub("ABV50", vote, round(v, 1), str(s.index[-1].date()))])


def _m_credit(today: date) -> dict:
    """信用：HY OAS 1M 变化（收窄=on / 显著走扩=off）。"""
    df = read_csv_or_empty(ROOT / "data/fred/volatility/volatility.csv")
    s = _col(df, "HY_OAS")
    if s is None:
        return _member("信用", [_sub("HY OAS 1M", None, None, None)])
    chg = _chg(s)
    if chg is None:
        return _member("信用", [_sub("HY OAS 1M", None, None, None)])
    chg_bp = chg * 100
    vote = True if chg_bp <= HY_OAS_ON_BP else (False if chg_bp >= HY_OAS_OFF_BP else None)
    return _member("信用", [_sub("HY OAS 1M", vote, round(chg_bp, 1), str(s.index[-1].date()))])


def _m_gex(today: date) -> dict:
    """期权结构：SPY net_gex 符号（正=dealer 多 gamma=稳定=on）。"""
    files = sorted((ROOT / "data/options_structure").glob("*.json"))
    if not files:
        return _member("期权结构", [_sub("SPY net_gex", None, None, None)])
    try:
        d = json.loads(files[-1].read_text())
        spy = d.get("symbols", {}).get("SPY", {})
        gex = spy.get("net_gex")
    except (json.JSONDecodeError, AttributeError):
        gex = None
    as_of = str(d.get("date", files[-1].stem))[:10] if files else None
    vote = (gex > 0) if isinstance(gex, (int, float)) else None
    val = round(gex, 1) if gex is not None else None
    return _member("期权结构", [_sub("SPY net_gex", vote, val, as_of)])


def _m_short(today: date) -> dict:
    """卖空（proxy）：SPY short ratio 5 日均值差（百分点）。"""
    df = read_csv_or_empty(ROOT / "data/short_selling/finra_daily.csv")
    s = _col(df, "SPY_short_ratio")
    if s is None or len(s) < SHORT_RATIO_WIN * 2:
        return _member("卖空", [_sub("SPY short ratio", None, None, None)])
    cur = s.iloc[-SHORT_RATIO_WIN:].mean()
    prev = s.iloc[-2 * SHORT_RATIO_WIN : -SHORT_RATIO_WIN].mean()
    diff_pp = (cur - prev) * 100
    vote = (
        True if diff_pp <= SHORT_RATIO_ON_PP else (False if diff_pp >= SHORT_RATIO_OFF_PP else None)
    )
    sub = _sub("SPY short ratio", vote, round(diff_pp, 2), str(s.index[-1].date()))
    return _member("卖空", [sub])


# ── 宏观卡成员 ──


def _macro_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """rates / liquidity / tips 三张基础表（derive_macro 的输入）。"""
    rates = read_csv_or_empty(ROOT / "data/fred/rates/rates.csv")
    liq = read_csv_or_empty(ROOT / "data/fred/liquidity/liquidity.csv")
    tips = read_csv_or_empty(ROOT / "data/fred/tips/tips.csv")
    return rates, liq, tips


def _x_policy(today: date) -> dict:
    """政策路径：最近未开会期的降息/加息概率（ZQ 期货隐含）。"""
    df = read_csv_or_empty(ROOT / "data/rate_expectations/fomc_probabilities.csv")
    sub = _sub("最近会期 prob_cut", None, None, None)
    if not df.empty and {"meeting_date", "prob_cut", "prob_hike"} <= set(df.columns):
        latest = str(df.index.max().date())
        rows = df.copy()
        rows["meeting_date"] = rows["meeting_date"].astype(str)
        upcoming = rows[rows["meeting_date"] >= latest]
        row = (upcoming if not upcoming.empty else rows).sort_values("meeting_date").iloc[0]
        pc, ph = row["prob_cut"], row["prob_hike"]
        if pd.notna(pc) and pd.notna(ph):
            vote = True if pc >= PROB_CUT_ON else (False if ph >= PROB_HIKE_OFF else None)
            sub = _sub("最近会期 prob_cut", vote, round(float(pc) * 100, 1), latest)
    return _member("政策路径", [sub])


def _x_netliq(today: date) -> dict:
    """净流动性：WALCL−RRP−TGA 1M 变化（复用 macro.py 派生，单位十亿美元）。"""
    _, liq, _ = _macro_frames()
    sub = _sub("净流动性 1M", None, None, None)
    if not liq.empty and {"WALCL", "RRPONTSYD", "WTREGEN"} <= set(liq.columns):
        derived = derive_macro(liq)
        s = _col(derived, "NET_LIQUIDITY")
        chg = _chg(s) if s is not None else None
        if chg is not None:
            b = chg / 1000  # 百万 → 十亿
            vote = True if b >= NET_LIQ_CHG_B else (False if b <= -NET_LIQ_CHG_B else None)
            sub = _sub("净流动性 1M", vote, round(b, 1), str(s.index[-1].date()))
    return _member("净流动性", [sub])


def _x_sofr_iorb(today: date) -> dict:
    """货币市场：SOFR−IORB 利差（bp），转负为宽、转正为紧。"""
    rates, _, _ = _macro_frames()
    sub = _sub("SOFR−IORB", None, None, None)
    if not rates.empty and {"SOFR", "IORB"} <= set(rates.columns):
        derived = derive_macro(rates)
        s = _col(derived, "SOFR_IORB_SPREAD_BP")
        if s is not None:
            v = float(s.iloc[-1])
            vote = True if v <= SOFR_IORB_ON_BP else (False if v >= SOFR_IORB_OFF_BP else None)
            sub = _sub("SOFR−IORB", vote, round(v, 1), str(s.index[-1].date()))
    return _member("货币市场", [sub])


def _x_curve(today: date) -> dict:
    """曲线：2s10s 水平（bp），深度倒挂=紧缩定价。"""
    rates, _, _ = _macro_frames()
    sub = _sub("2s10s", None, None, None)
    if not rates.empty and {"DGS10", "DGS2"} <= set(rates.columns):
        derived = derive_macro(rates)
        s = _col(derived, "SPREAD_2S10S")
        if s is not None:
            v = float(s.iloc[-1]) * 100
            vote = True if v > SPREAD_2S10S_ON else (False if v < SPREAD_2S10S_OFF else None)
            sub = _sub("2s10s", vote, round(v, 1), str(s.index[-1].date()))
    return _member("曲线", [sub])


def _x_real_rate(today: date) -> dict:
    """实际利率：DFII10 1M 变化（bp），实际利率回落=金融条件转松。"""
    _, _, tips = _macro_frames()
    s = _col(tips, "DFII10")
    sub = _sub("DFII10 1M", None, None, None)
    if s is not None:
        chg = _chg(s)
        if chg is not None:
            bp = chg * 100
            vote = True if bp <= -DFII10_CHG_BP else (False if bp >= DFII10_CHG_BP else None)
            sub = _sub("DFII10 1M", vote, round(bp, 1), str(s.index[-1].date()))
    return _member("实际利率", [sub])


def _x_tga(today: date) -> dict:
    """财政：TGA 1M 变化（十亿美元），余额下降=向市场注水。"""
    _, liq, _ = _macro_frames()
    s = _col(liq, "WTREGEN")
    sub = _sub("TGA 1M", None, None, None)
    if s is not None:
        chg = _chg(s)
        if chg is not None:
            b = chg / 1000
            vote = True if b <= -TGA_CHG_B else (False if b >= TGA_CHG_B else None)
            sub = _sub("TGA 1M", vote, round(b, 1), str(s.index[-1].date()))
    return _member("财政", [sub])


# ── 组装 ──


def compute(today: date | None = None) -> dict:
    """计算两级共识卡（纯读 CSV，不落盘）。"""
    today = today or date.today()
    market = [f(today) for f in (_m_trend, _m_vol, _m_breadth, _m_credit, _m_gex, _m_short)]
    macro = [f(today) for f in (_x_policy, _x_netliq, _x_sofr_iorb, _x_curve, _x_real_rate, _x_tga)]
    for group in (market, macro):
        _apply_staleness(group, today)
    ref = max(
        (m["as_of"] for g in (market, macro) for m in g if m["as_of"]),
        default=None,
    )
    return {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "ref_date": ref,
        "market": _aggregate(market, MKT_ON, MKT_OFF),
        "macro": _aggregate(macro, MAC_ON, MAC_OFF),
        "page_links": PAGE_LINKS,
    }


def append_history(result: dict, intraday: bool = False) -> Path | None:
    """当日状态 upsert 进 history.csv。

    source=final 总是覆盖（收盘定稿）；source=intraday 只写无行或已有行同为
    intraday 的情况——盘中临时值绝不覆盖定稿行。
    """
    if not result.get("ref_date"):
        return None
    out = ROOT / "data/consensus/history.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["date", "market_state", "macro_state", "market_votes", "macro_votes", "source"]
    row = {
        "date": result["ref_date"],
        "market_state": result["market"]["state"],
        "macro_state": result["macro"]["state"],
        "market_votes": _votes_tag(result["market"]),
        "macro_votes": _votes_tag(result["macro"]),
        "source": "intraday" if intraday else "final",
    }
    old = read_csv_or_empty(out, index_col=None)  # date 保留为普通列（不用索引）
    if not old.empty:
        existing = old[old["date"] == row["date"]]
        if not existing.empty and existing.iloc[0]["source"] == "final" and intraday:
            return out  # 盘中不覆盖定稿
        old = old[old["date"] != row["date"]]
    new = pd.concat([old, pd.DataFrame([row])], ignore_index=True)[cols]
    new.to_csv(out, index=False)
    return out


def _votes_tag(card: dict) -> str:
    """票面压缩串：'趋势:on;波动率:abst;…'（广度统计与回测的原料）。"""
    tag = {"on": "on", "off": "off", None: "abst"}
    return ";".join(
        f"{m['name']}:{'absent' if not m['present'] else tag[m['vote']]}" for m in card["members"]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="多源共识层")
    ap.add_argument("--intraday", action="store_true", help="盘中临时值（不覆盖定稿行）")
    ap.add_argument("--no-save", action="store_true", help="只算不落盘")
    args = ap.parse_args()
    result = compute()
    if not args.no_save:
        append_history(result, intraday=args.intraday)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
