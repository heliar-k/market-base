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

import re

import pandas as pd

from src.analysis_utils import chg_prev as _chg_prev
from src.analysis_utils import read_csv_or_empty, release_dates
from src.config import ROOT, config

FRED_DIR = ROOT / "data" / "fred"

HOLD_LABELS = {
    "TIC_HOLD_TOTAL": "海外持仓总额",
    "TIC_HOLD_OFFICIAL": "海外官方持仓",
    "TIC_HOLD_JAPAN": "日本",
    "TIC_HOLD_CHINA": "中国",
    "TIC_HOLD_SAUDI": "沙特",
    "TIC_HOLD_UAE": "阿联酋",
    "TIC_HOLD_UK": "英国",
    "TIC_HOLD_FRANCE": "法国",
    "TIC_HOLD_BELGIUM": "比利时",
    "TIC_HOLD_IRELAND": "爱尔兰",
    "TIC_HOLD_LUXEMBOURG": "卢森堡",
    "TIC_HOLD_SWISS": "瑞士",
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


def rank_label(rank: float | None) -> str:
    """分位 → 中文读法（0 分位不是「最低分位」，极端值要说人话）。

    后端一次算好，卡片副标题与研判段共用（前端不重算，避免两处漂移）。
    """
    if rank is None:
        return ""
    if rank <= 1:
        return "全历史最低"
    if rank >= 99:
        return "全历史最高"
    return f"全历史 {rank:.0f} 分位"


def _pct_rank(s: pd.Series, v: float) -> float | None:
    """v 在序列 s 全历史里的分位（%，严格 < 口径）。

    给「当前读数处于历史什么位置」用：阈值判断（<23% / >22%）会随时间失效，
    分位不会。严格 < 与 credit_analysis._pct 同口径（并列值多时 <= 会虚高）。
    """
    s = s.dropna()
    if s.empty:
        return None
    return round(float((s < v).mean() * 100), 1)


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
    """信号一：海外需求（结论 = 结构性需求判断 / 依据 = 持仓 + 净买入 + 官方占比）。"""
    h, share, net = cards["hold_total"], cards["official_share"], cards["net_total"]
    flow = f"海外持仓总额 {h['value']:.2f} 万亿美元"
    if h.get("chg_1y_b") is not None:
        d = h["chg_1y_b"] * 10  # $B → 亿
        flow += f"，近一年{'增持' if d >= 0 else '减持'} {abs(d):,.0f}亿"
    flow += (
        f"；当月净{'买入' if net['value'] >= 0 else '卖出'} "
        f"{abs(net['value']) * 10:,.0f}亿"
    )
    if net_12m is not None:
        flow += (
            f"，近 12 个月累计净{'买入' if net_12m >= 0 else '卖出'} "
            f"{abs(net_12m) * 10:,.0f}亿"
        )
    basis = [flow]
    verdict = "海外持仓数据不足。"
    if share.get("value") is not None:
        rank = share.get("pct_rank")
        # 分位优先：阈值 <23% 自 2015-08 起连续 131 个月都在下方，单用阈值永远
        # 输出同一句话——它已不含信息。分位才能区分「低」与「史上最低」。
        pos = f"（{share['pct_label']}）" if share.get("pct_label") else ""
        basis.append(f"海外官方持仓占总未偿债务 {share['value']}%{pos}")
        if rank is not None and rank <= 5:
            verdict = (
                "官方占比处于全历史最低区，官方结构性需求已退到边缘"
                "——长端利率几乎完全依赖私人部门（价格敏感型）接盘，"
                "期限溢价易上难下。"
            )
        elif share["value"] < OFFICIAL_SHARE_WARN:
            verdict = (
                f"官方占比低于 {OFFICIAL_SHARE_WARN:.0f}% 警戒线，官方结构性需求在退坡"
                "——长端利率对私人部门（价格敏感型）接盘的依赖上升，"
                "期限溢价易上难下。"
            )
        else:
            verdict = "官方需求尚在安全区，海外端暂未构成边际压力。"
    return f"{verdict}\n" + " · ".join(basis) + "。"


def signal_countries(holdings: list[dict]) -> str:
    """信号二：国别结构（结论 = 减持/企稳判断 / 依据 = 日本中国持仓数字）。"""
    d = {r["key"]: r for r in holdings}
    jp, cn = d.get("TIC_HOLD_JAPAN", {}), d.get("TIC_HOLD_CHINA", {})

    def _t_or_dash(b):
        return "—" if b is None else f"{b / 1000:.2f} 万亿"

    basis = (
        f"日本仍是最大海外持有国（{_t_or_dash(jp.get('value_b'))}美元），"
        f"中国 {_t_or_dash(cn.get('value_b'))}美元"
    )
    if cn.get("chg_1y_b") is not None:
        basis += (
            f"（近一年{'增持' if cn['chg_1y_b'] >= 0 else '减持'} "
            f"{abs(cn['chg_1y_b']) * 10:,.0f}亿）"
        )
    if isinstance(cn.get("chg_1y_b"), (int, float)) and cn["chg_1y_b"] < 0:
        verdict = (
            "中国持仓延续下降趋势，储备多元化（黄金/非美资产）方向未变；"
            "海湾国家（沙特/阿联酋）持仓随油价财政盈余同变动，作为边际买家稳定性较弱。"
        )
    else:
        verdict = "中国持仓企稳，国别层面暂无系统性减持信号。"
    return f"{verdict}\n{basis}。"


def signal_issuance(cards: dict, refunding: dict) -> str:
    """信号三：发行结构（结论 = 短债占比影响 / 依据 = Bill 占比 + 再融资指引）。

    短债占比判断走历中分位（阈值 22% 在全历史 40% 的月份都被突破，当不了「偏高」
    的依据）；附息债规模措辞从 QRA 声明正文解析，不写死——写死会在下个
    季度财政部改口后变成假消息。
    """
    bs = cards["bill_share"]
    rank = bs.get("pct_rank")
    basis = f"Bill 占可流通债务 {bs['value']}%"
    if bs.get("pct_label"):
        basis += f"（{bs['pct_label']}）"
    if bs.get("chg_1y") is not None:
        basis += f" · 较一年前 {bs['chg_1y']:+.1f}pp"
    guide = refunding.get("coupon_sizes")
    if refunding.get("quarter"):
        verb = {
            "maintain": "维持附息债拍卖规模不变",
            "increase": "上调附息债拍卖规模",
            "decrease": "下调附息债拍卖规模",
        }.get(guide or "", "未明确附息债拍卖规模指引")
        basis += f" · 最新季度再融资声明（{refunding['quarter']}）{verb}"
    if rank is not None and rank >= 70:
        verdict = (
            "短债占比处于历史高位，财政部以 Bill 吸收融资需求、压长端供给"
            "——对长端利率是短期缓冲，但展期风险向未来集中。"
        )
    elif rank is not None and rank <= 30:
        verdict = "短债占比处于历史低位，长端供给占比回升，期限溢价压力上升。"
    else:
        verdict = "短债占比处于历史常态区间，发行结构未见明显扭曲。"
    if guide == "maintain":
        verdict += "按最新声明指引，长端暂无增量供给压力。"
    elif guide == "increase":
        verdict += "声明已给出增量供给指引，长端面临上拍卖规模压力。"
    return f"{verdict}\n{basis}。"


def holdings_table(tic: pd.DataFrame) -> list[dict]:
    """持仓明细：最新值（$B）+ 1Y 变化（$B）+ 发布时间。"""
    rel = release_dates(FRED_DIR)
    sid = config.fred_series["tic"]
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
                "released": (rel.get(sid.get(key, "")) or "")[:10],
            }
        )
    return rows


def holdings_history(tic: pd.DataFrame, months: int = 120) -> dict:
    """海外持仓（总额/日本/中国/主要欧洲国家，$T）近 months 个月。"""
    total = tic["TIC_HOLD_TOTAL"].dropna().tail(months)
    r = lambda v: _t(float(v)) if pd.notna(v) else None  # noqa: E731
    out = {
        "dates": [d.strftime("%Y-%m-%d") for d in total.index],
        "total": [r(v) for v in total],
    }
    country_keys = {
        "TIC_HOLD_JAPAN": "japan",
        "TIC_HOLD_CHINA": "china",
        "TIC_HOLD_UK": "uk",
        "TIC_HOLD_FRANCE": "france",
        "TIC_HOLD_BELGIUM": "belgium",
        "TIC_HOLD_IRELAND": "ireland",
        "TIC_HOLD_LUXEMBOURG": "luxembourg",
        "TIC_HOLD_SWISS": "swiss",
    }
    for key, short in country_keys.items():
        if key in tic:
            out[short] = [r(v) for v in tic[key].reindex(total.index)]
    return out


def mspd_history(mspd: pd.DataFrame) -> dict:
    """未偿债务结构历史（月度全量）：各品种占比（% 累积堆叠）+ 绝对额（$T）。"""
    if mspd.empty:
        return {}
    out = {"dates": [d.strftime("%Y-%m-%d") for d in mspd.index]}
    total = mspd["MARKETABLE_TOTAL"]
    for col in ("BILLS", "NOTES", "BONDS", "TIPS", "FRN"):
        if col in mspd:
            out[col.lower()] = [
                _t(float(v)) if pd.notna(v) else None for v in mspd[col]
            ]
            out[f"pct_{col.lower()}"] = [
                float(v) / float(m) * 100 if pd.notna(v) and pd.notna(m) and m else None
                for v, m in zip(mspd[col], total, strict=True)
            ]
    return out


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
                    "as_of": last.name.strftime("%Y-%m-%d"),
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
        # 附息债规模指引：从声明正文抽（研判段不能写死「维持不变」）
        "coupon_sizes": _coupon_size_guided(str(last["body"])),
    }


def _coupon_size_guided(body: str) -> str | None:
    """QRA 声明正文 → 附息债拍卖规模指引（maintain / increase / decrease / None）。

    锁定句式 "Treasury anticipates maintaining nominal coupon and FRN auction
    sizes"；措辞改了就是 None（页面显示「未明确」），绝不猜。
    """
    m = re.search(
        r"(maintain|maintaining|increase|increasing|decrease|decreasing|reduce|"
        r"reducing)[^.]{0,80}?(nominal coupon|coupon and FRN|auction size)",
        body,
        re.I,
    )
    if not m:
        return None
    w = m.group(1).lower()
    if w.startswith("maintain"):
        return "maintain"
    if w.startswith("increase"):
        return "increase"
    return "decrease"


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
    # 1Y 变化只能算在 MSPD 月频轴上：日频派生序列自 2026-08 才开算（仅 30+ 行），
    # 取 250 个交易日前的值永远为 None（卡片副标题永远只剩一个日期）。
    bs_mspd = (
        mspd["BILL_SHARE"].dropna() if "BILL_SHARE" in mspd else pd.Series(dtype=float)
    )
    bs_yoy = _chg_prev(bs_mspd, 12)
    # 全历史分位（卡片与研判共用，后端只算一次）
    share_rank = _pct_rank(share, float(share.iloc[-1])) if not share.empty else None
    bs_rank = _pct_rank(bs_mspd, float(bs_pair[0])) if bs_pair else None

    cards = {
        "hold_total": {
            "value": _t(hold_pair[0]),
            "chg_1y_b": _yoy_chg_b(tic["TIC_HOLD_TOTAL"]),
            "as_of": hold_pair[1].strftime("%Y-%m-%d"),
        },
        "official_share": {
            "value": round(float(share.iloc[-1]), 1) if not share.empty else None,
            "as_of": share.index[-1].strftime("%Y-%m-%d") if not share.empty else None,
            # <23% 阈值自 2015-08 起连续 131 个月都在下方，单靠阈值判断永远为真、
            # 不含信息；真正的新信息是当前值在全历史里的位置。
            "pct_rank": share_rank,
            "pct_label": rank_label(share_rank),
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
            "pct_rank": bs_rank,
            "pct_label": rank_label(bs_rank),
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
        "mspd_history": mspd_history(mspd),
        "refunding": refunding,
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印摘要
    import json

    out = generate_treasury_overview()
    assert "cards" in out, out.get("error")
    assert out["cards"]["official_share"]["value"] is not None
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:3000])
