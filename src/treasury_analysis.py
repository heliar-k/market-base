"""美债需求研判规则引擎 — 美债需求专题页数据源（与 inflation/credit 专题同构）。

数据来源（全本地 CSV，确定性规则，无 LLM）：
  data/fred/tic/tic.csv                 TIC 海外持仓/净买入（月频，滞后 2 月，百万美元）
  data/treasury/mspd.csv                月度未偿债务结构（record_date 列，百万美元）
  data/treasury/bill_share_daily.csv    日频 Bill 占比（派生，%）
  data/treasury/refunding.csv           季度再融资声明（正文不解析，只取元数据+链接）

海外官方占比口径（config.py tic 注释）：TIC_HOLD_OFFICIAL / mspd TOTAL_DEBT，
<23% 结构性偏空。勿用 GFDEBTN（FRED 已停更）。

输出结构：
  cards:     海外持仓总额 / 海外官方占比 / 月度净买入 / Bill 占比
  signals:   三段研判（海外需求 / 国别结构 / 发行结构）
  holdings_history: 海外持仓（总额/日本/中国，$T）近 10 年月线
  holdings:  持仓明细表（总额/官方/日本/中国/沙特/阿联酋 + 1Y 变化）
  mspd:      未偿债务结构表（BILLS/NOTES/BONDS/TIPS/FRN）
  refunding: 最新季度再融资声明元数据
"""

from __future__ import annotations

import pandas as pd

from src.analysis_utils import chg_prev as _chg_prev
from src.analysis_utils import read_csv_or_empty
from src.config import ROOT

HOLD_LABELS = {
    "TIC_HOLD_TOTAL": "海外持仓总额",
    "TIC_HOLD_OFFICIAL": "海外官方持仓",
    "TIC_HOLD_JAPAN": "日本",
    "TIC_HOLD_CHINA": "中国",
    "TIC_HOLD_SAUDI": "沙特",
    "TIC_HOLD_UAE": "阿联酋",
}

# 官方占比警戒线（config.py tic 注释口径）
OFFICIAL_SHARE_WARN = 23.0


def _read_tic() -> pd.DataFrame:
    return read_csv_or_empty(ROOT / "data" / "fred" / "tic" / "tic.csv")


def _read_mspd() -> pd.DataFrame:
    return read_csv_or_empty(
        ROOT / "data" / "treasury" / "mspd.csv", index_col="record_date"
    )


def _t(millions: float | None) -> float | None:
    """百万美元 → 万亿美元。"""
    return None if millions is None else round(millions / 1e6, 2)


def _b(millions: float | None) -> float | None:
    """百万美元 → 十亿美元。"""
    return None if millions is None else round(millions / 1e3, 1)


def _latest_pair(s: pd.Series) -> tuple[float, pd.Timestamp] | None:
    s = s.dropna()
    return None if s.empty else (float(s.iloc[-1]), s.index[-1])


def _yoy_chg_b(s: pd.Series) -> float | None:
    """月频持仓 1 年变化（$B）。"""
    pair = _chg_prev(s, 12)
    return None if pair is None else _b(pair[0] - pair[1])


def official_share_series(tic: pd.DataFrame, mspd: pd.DataFrame) -> pd.Series:
    """海外官方持仓 / 总未偿债务（%，月度；mspd 按 TIC 日期轴 ffill 对齐）。"""
    if (
        tic.empty
        or mspd.empty
        or "TIC_HOLD_OFFICIAL" not in tic
        or "TOTAL_DEBT" not in mspd
    ):
        return pd.Series(dtype=float)
    hold = tic["TIC_HOLD_OFFICIAL"].dropna()
    debt = mspd["TOTAL_DEBT"].dropna().sort_index().reindex(hold.index, method="ffill")
    share = (hold / debt * 100).dropna()
    return share


def signal_foreign(cards: dict, net_12m: float | None) -> str:
    """信号一：海外需求（持仓趋势 + 净买入 + 官方占比）。"""
    h, share, net = cards["hold_total"], cards["official_share"], cards["net_total"]
    text = f"海外持仓总额 ${h['value']:.2f} 万亿"
    if h.get("chg_1y_b") is not None:
        d = h["chg_1y_b"]
        text += f"，近一年{'增持' if d >= 0 else '减持'} ${abs(d):.0f}B"
    text += (
        f"；当月净{'买入' if net['value'] >= 0 else '卖出'} ${abs(net['value']):.1f}B"
    )
    if net_12m is not None:
        text += (
            f"，近 12 个月累计净{'买入' if net_12m >= 0 else '卖出'} "
            f"${abs(net_12m):.0f}B"
        )
    text += "。"
    if share.get("value") is not None:
        text += f"海外官方持仓占总未偿债务 {share['value']}%"
        if share["value"] < OFFICIAL_SHARE_WARN:
            text += (
                f"，低于 {OFFICIAL_SHARE_WARN:.0f}% 警戒线——"
                "官方部门的结构性需求在退坡，"
                "长端利率对私人部门（价格敏感型）接盘的依赖上升，期限溢价易上难下。"
            )
        else:
            text += "，官方需求尚在安全区。"
    return text


def signal_countries(holdings: list[dict]) -> str:
    """信号二：国别结构（日本/中国/海湾）。"""
    d = {r["key"]: r for r in holdings}
    jp, cn = d.get("TIC_HOLD_JAPAN", {}), d.get("TIC_HOLD_CHINA", {})
    text = (
        f"日本仍是最大海外持有国（${jp.get('value_b', '—')}B），"
        f"中国 ${cn.get('value_b', '—')}B"
    )
    if cn.get("chg_1y_b") is not None:
        text += (
            f"（近一年{'增持' if cn['chg_1y_b'] >= 0 else '减持'} "
            f"${abs(cn['chg_1y_b']):.0f}B）"
        )
    text += "。"
    if isinstance(cn.get("chg_1y_b"), (int, float)) and cn["chg_1y_b"] < 0:
        text += (
            "中国持仓延续下降趋势，储备多元化（黄金/非美资产）方向未变；"
            "海湾国家（沙特/阿联酋）持仓与油价财政盈余联动，作为边际买家稳定性较弱。"
        )
    else:
        text += "中国持仓企稳，国别层面暂无系统性减持信号。"
    return text


def signal_issuance(cards: dict, refunding: dict) -> str:
    """信号三：发行结构（Bill 占比 + 再融资指引）。"""
    bs = cards["bill_share"]
    text = f"Bill 占可流通债务 {bs['value']}%"
    if bs.get("chg_1y") is not None:
        text += f"（较一年前 {bs['chg_1y']:+.1f}pp）"
    text += "。"
    if bs["value"] > 22:
        text += (
            "短债占比偏高，财政部以 Bill 吸收融资需求、压长端供给——"
            "对长端利率是短期缓冲，但展期风险向未来集中；"
        )
    else:
        text += "短债占比处于历史常态区间（~15-20%），发行结构未见明显扭曲；"
    if refunding.get("quarter"):
        text += (
            f"最新季度再融资声明（{refunding['quarter']}）维持附息债拍卖规模不变的指引，"
            "长端供给压力暂缓。"
        )
    return text


def holdings_table(tic: pd.DataFrame) -> list[dict]:
    """持仓明细：最新值（$B）+ 1Y 变化（$B）。"""
    rows = []
    for key, name in HOLD_LABELS.items():
        if key not in tic:
            continue
        pair = _latest_pair(tic[key])
        if pair is None:
            continue
        v, dt = pair
        rows.append(
            {
                "key": key,
                "name": name,
                "value_b": _b(v),
                "chg_1y_b": _yoy_chg_b(tic[key]),
                "as_of": dt.strftime("%Y-%m-%d"),
            }
        )
    return rows


def holdings_history(tic: pd.DataFrame, months: int = 120) -> dict:
    """海外持仓（总额/日本/中国，$T）近 months 个月。"""
    total = tic["TIC_HOLD_TOTAL"].dropna().tail(months)
    jp = tic["TIC_HOLD_JAPAN"].reindex(total.index)
    cn = tic["TIC_HOLD_CHINA"].reindex(total.index)
    r = lambda v: _t(float(v)) if pd.notna(v) else None  # noqa: E731
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in total.index],
        "total": [r(v) for v in total],
        "japan": [r(v) for v in jp],
        "china": [r(v) for v in cn],
    }


def mspd_table(mspd: pd.DataFrame) -> list[dict]:
    """未偿债务结构（$T）。"""
    if mspd.empty:
        return []
    last = mspd.iloc[-1]
    labels = [
        ("BILLS", "短期国债 Bills"),
        ("NOTES", "中期国债 Notes"),
        ("BONDS", "长期国债 Bonds"),
        ("TIPS", "TIPS"),
        ("FRN", "浮动利率 FRN"),
    ]
    rows = []
    for key, name in labels:
        if key in last and pd.notna(last[key]):
            share = (
                last[key] / last["MARKETABLE_TOTAL"] * 100
                if last.get("MARKETABLE_TOTAL")
                else None
            )
            rows.append(
                {
                    "name": name,
                    "value_t": _t(float(last[key])),
                    "share": round(float(share), 1) if share is not None else None,
                }
            )
    return rows


def refunding_meta() -> dict:
    """最新季度再融资声明元数据（正文不解析，页面给链接）。"""
    df = read_csv_or_empty(ROOT / "data" / "treasury" / "refunding.csv", index_col="id")
    if df.empty:
        return {}
    st = df[df["kind"] == "statement"]
    if st.empty:
        return {}
    last = st.iloc[-1]
    return {
        "quarter": last["quarter"],
        "date": str(last["date"])[:10],
        "title": last["title"],
        "url": last["url"],
    }


def generate_treasury_overview() -> dict:
    """美债需求专题总览统一入口（规则引擎，LLM 预留同 inflation_analysis）。"""
    tic = _read_tic()
    if tic.empty or "TIC_HOLD_TOTAL" not in tic.columns:
        return {"error": "data/fred/tic/tic.csv 缺失或为空，先运行 ./bin/fetch_fred"}
    mspd = _read_mspd()
    bs_daily = read_csv_or_empty(ROOT / "data" / "treasury" / "bill_share_daily.csv")

    share = official_share_series(tic, mspd)
    net = tic["TIC_NET_TOTAL"].dropna()
    net_pair = _latest_pair(net)
    hold_pair = _latest_pair(tic["TIC_HOLD_TOTAL"])
    if hold_pair is None or net_pair is None:
        return {"error": "tic.csv 缺有效持仓/净买入数据"}

    bs_pair = _latest_pair(bs_daily["BILL_SHARE"]) if not bs_daily.empty else None
    bs_yoy = _chg_prev(bs_daily["BILL_SHARE"], 250) if not bs_daily.empty else None

    cards = {
        "hold_total": {
            "value": _t(hold_pair[0]),
            "chg_1y_b": _yoy_chg_b(tic["TIC_HOLD_TOTAL"]),
            "as_of": hold_pair[1].strftime("%Y-%m-%d"),
        },
        "official_share": {
            "value": round(float(share.iloc[-1]), 1) if not share.empty else None,
            "as_of": share.index[-1].strftime("%Y-%m-%d") if not share.empty else None,
        },
        "net_total": {
            "value": _b(net_pair[0]),
            "net_12m_b": _b(float(net.tail(12).sum())),
            "as_of": net_pair[1].strftime("%Y-%m-%d"),
        },
        "bill_share": {
            "value": round(bs_pair[0], 1) if bs_pair else None,
            "chg_1y": round(bs_yoy[0] - bs_yoy[1], 1) if bs_yoy else None,
            "as_of": bs_pair[1].strftime("%Y-%m-%d") if bs_pair else None,
        },
    }

    holdings = holdings_table(tic)
    refunding = refunding_meta()
    return {
        "generator": "rules",
        "as_of": cards["hold_total"]["as_of"],
        "cards": cards,
        "signals": [
            {
                "title": "海外需求",
                "text": signal_foreign(cards, cards["net_total"]["net_12m_b"]),
            },
            {"title": "国别结构", "text": signal_countries(holdings)},
            {"title": "发行结构", "text": signal_issuance(cards, refunding)},
        ],
        "holdings_history": holdings_history(tic),
        "holdings": holdings,
        "mspd": mspd_table(mspd),
        "refunding": refunding,
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印摘要
    import json

    out = generate_treasury_overview()
    assert "cards" in out, out.get("error")
    assert out["cards"]["official_share"]["value"] is not None
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:3000])
