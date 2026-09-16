"""Polymarket 分析层共享读取（供 daily_brief / 专题分析 / geo 页复用）。

职责：读 data/polymarket/ 最新快照 + history.csv 概率时序，提供事件过滤、
7 日变化、阈值阶梯、到期市场过滤、geo 主题聚类与能源地缘块。
"给人看"的叙事入口也在这里（规则引擎，只读不写盘）。

fed_analysis.market_odds 与 assets_analysis._polymarket 维护各自的快照
读取路径（前者含 `.1` 后缀 bfill 归一）；chg7d 已回收共享。

用法:
    from src.polymarket_analysis import snapshot, series_for, chg7d
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pandas as pd

from src.config import ROOT

DATA = ROOT / "data" / "polymarket"


def snapshot() -> dict | None:
    """最新可解析快照（坏 JSON 回退前一日）；无文件返回 None。
    分类过滤由 events_matching 做（geo_overview 等消费方自行过滤）。
    """
    for f in sorted(DATA.glob("20*.json"), reverse=True):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def events_matching(
    snap: dict | None,
    pattern: str | re.Pattern | None = None,
    categories: tuple[str, ...] = (),
) -> list[dict]:
    """快照事件过滤：分类 + 标题正则（不区分大小写）。

    按事件 volume 降序返回。pattern 是纯字符串时按不区分大小写包含处理
    （调用方传已编译正则可控制词边界）。
    """
    if not snap:
        return []
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    out = []
    for e in snap.get("events") or []:
        if categories and e.get("category") not in categories:
            continue
        if rx and not rx.search(e.get("title") or ""):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.get("volume24hr") or 0, reverse=True)


def series_for(ids: set[str]) -> dict[str, list[dict]]:
    """history.csv 宽表 → {market_id: [{date, value}, …]}（升序，仅保留命中 id）。

    同日重复 id 列（pandas `.1` 后缀）bfill 归一（同 fed_analysis 口径）。
    """
    path = DATA / "history.csv"
    if not path.exists() or not ids:
        return {}
    h = pd.read_csv(path)
    cols_by_id: dict[str, list[str]] = {}
    for col in h.columns:
        if col == "date":
            continue
        base = str(col).split(".")[0]
        if base in ids:
            cols_by_id.setdefault(base, []).append(col)
    out: dict[str, list[dict]] = {}
    for base, cols in cols_by_id.items():
        val = h[cols].bfill(axis=1).iloc[:, 0]
        s = pd.DataFrame({"date": h["date"], "value": val}).dropna(subset=["value"])
        out[base] = [
            {"date": d, "value": round(float(v), 4)}
            for d, v in zip(s["date"], s["value"])
        ]
    return out


def chg7d(points: list[dict] | None) -> float | None:
    """概率 7 日变化（百分点）：最新值 − 距 7 天前最近的观测；不足 2 点返回 None。"""
    if not points or len(points) < 2:
        return None
    last = points[-1]
    t0 = datetime.strptime(last["date"], "%Y-%m-%d") - timedelta(days=7)
    ref = min(
        points[:-1], key=lambda p: abs(datetime.strptime(p["date"], "%Y-%m-%d") - t0)
    )
    return round((last["value"] - ref["value"]) * 100, 1)


_THRESHOLD_RX = re.compile(r"(?:more than|at least|above)\s+(\d+(?:\.\d+)?)\s*%")


def energy_block() -> dict | None:
    """能源地缘风险（霍尔木兹海峡事件卡，commodities 页数据源）。

    标题含 hormuz 的事件全部纳入（上限 6 个），市场按概率降序、
    到期市场过滤；7 日变化来自 history.csv。无快照/无事件返回 None。
    """
    snap = snapshot()
    evs = events_matching(snap, pattern=r"hormuz")
    if not evs:
        return None
    ids = {str(m["id"]) for e in evs for m in e.get("markets") or []}
    hist = series_for(ids)
    events_out = []
    for e in evs[:6]:
        mkts = sorted(
            (
                {
                    "label": (m.get("question") or "").removesuffix("?"),
                    "prob": m["prob_yes"],
                    "chg7d": chg7d(hist.get(str(m["id"]))),
                }
                for m in active_markets(e, snap.get("as_of"))
            ),
            key=lambda x: x["prob"],
            reverse=True,
        )
        events_out.append(
            {
                "title": e["title"],
                "end_date": e.get("end_date"),
                "volume24hr": e.get("volume24hr"),
                "markets": mkts,
            }
        )
    return {"as_of": snap.get("as_of"), "events": events_out}


# ── 地缘与政治风险专题（geo 页）─────────────────────────────────────────

# 主题聚类：关键词 → (key, 名称)；匹配 title 小写包含，先命中先归类。
# 热战主题在前、选举居后（「Israel 总理选举」归以色列战线而非选举）；
# 均未命中归「其它」（美国入侵古巴/格陵兰等孤立事件）。
GEO_TOPICS: list[tuple[str, str, tuple[str, ...]]] = [
    ("iran", "伊朗与霍尔木兹", ("iran", "hormuz", "pahlavi", "uranium")),
    ("israel", "以色列战线", ("israel", "lebanon", "yemen", "gaza")),
    ("taiwan", "台海", ("taiwan",)),
    ("russia_ukraine", "俄乌", ("russia", "ukraine", "putin", "zelenskyy", "nato")),
    (
        "election",
        "选举",
        (
            "election",
            "president",
            "senate",
            "governor",
            "mayor",
            "parliament",
            "duma",
            "seat",
            "primary",
        ),
    ),
]


def geo_topic_of(event: dict) -> tuple[str, str]:
    """geo/policy 事件 → (主题 key, 名称)；无命中归 (other, 其它)。"""
    t = (event.get("title") or "").lower()
    for key, name, kws in GEO_TOPICS:
        if any(k in t for k in kws):
            return key, name
    return "other", "其它"


# 主题内语义归类：(key, 中文名, 关键词)，
# 匹配「事件标题 + 市场问题」小写包含，先命中先归类。
# 目的：页面不直接展示 Polymarket 英文原文，而是按中文语义分组聚合后画线。
# ⚠ 关键词规则会被新事件击穿：未命中市场归 MISC_CLUSTER，geo_overview 会把它们
#   记入返回值 unmatched 并生成⚠叙事——看到告警就回来往对应主题的元组里补词。
MISC_CLUSTER = ("misc", "其它事件")

GEO_CLUSTERS: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {
    "iran": [
        ("hormuz", "霍尔木兹航运", ("hormuz",)),
        (
            "military",
            "停火与军事行动",
            (
                "ceasefire",
                "ground operation",
                "declare war",
                "invade",
                "airspace closure",
                "military",
                "target ukraine",
            ),
        ),
        ("nuclear", "核问题", ("uranium", "enrich", "nuclear")),
        (
            "regime",
            "政权走向",
            ("leadership", "pahlavi", "khamenei", "leader", "enter iran"),
        ),
        ("talks", "美伊谈判", ("peace talks", "deal", "us-iran", "agreement")),
    ],
    "israel": [
        ("lebanon", "黎巴嫩战线", ("lebanon",)),
        ("yemen", "也门方向", ("yemen",)),
        ("politics", "以国内政局", ("prime minister", "election")),
        ("strike", "空袭与外交承认", ("airspace", "strike", "recognize")),
    ],
    "taiwan": [("clash", "军事冲突", ("invade", "clash", "military"))],
    "russia_ukraine": [
        (
            "ceasefire",
            "停火与和平协议",
            (
                "ceasefire",
                "peace deal",
                "peace combo",
                "peace referendum",
                "cede territory",
                "sovereignty over",
            ),
        ),
        (
            "talks",
            "外交接触",
            (
                "diplomatic meeting",
                "peace talks",
                "talk to putin",
                "visit ukraine",
                "meet",
            ),
        ),
        ("nato", "北约与军事对抗", ("nato", "military clash")),
        ("advance", "俄军占领推进", ("capture",)),
        ("regime", "俄乌政局", ("putin out", "zelenskyy out")),
        (
            "election",
            "俄议会选举",
            (
                "parliamentary",
                "legislative",
                "duma",
                "united russia",
                "yabloko",
                "seats",
            ),
        ),
    ],
    "election": [
        ("us_midterm", "美国中期选举", ("midterm", "senate seats", "governor")),
        ("france", "法国大选", ("french",)),
        ("brazil", "巴西大选", ("brazil",)),
        ("us2028", "2028 美国大选", ("2028",)),
        ("local", "地方选举", ("berlin", "mayor", "state election", "primary")),
        ("presidential", "总统大选", ("president",)),
    ],
    "other": [("muscle", "美军领土动作", ("invade", "cuba", "greenland"))],
}


def geo_cluster_of(topic_key: str, event: dict, market: dict) -> tuple[str, str]:
    """市场 → 主题内中文归类；无命中归 MISC_CLUSTER（unmatched 告警源）。"""
    text = f"{event.get('title') or ''} {market.get('question') or ''}".lower()
    for key, name, kws in GEO_CLUSTERS.get(topic_key, []):
        if any(k in text for k in kws):
            return key, name
    return MISC_CLUSTER


def active_markets(event: dict, as_of: str | None) -> list[dict]:
    """事件内未过期市场（end_date ≥ 快照日）；到期市场的概率滞留旧值（常是 0/1），
    会污染焦点提取与阶梯展示。无 end_date 视为活跃。"""
    a = str(as_of or "")[:10]
    return [
        m
        for m in event.get("markets") or []
        if m.get("prob_yes") is not None
        and (not a or not m.get("end_date") or str(m["end_date"])[:10] >= a)
    ]


def geo_overview() -> dict | None:
    """地缘与政治风险总览（geo 页数据源）；无快照/无 geo+policy 事件返回 None。

    结构：{as_of, signals, topics: [{key, name, volume24hr,
    clusters, headline, events}]}。
    页面主视图是 clusters（主题内中文语义归类）：同归类内市场概率均值 +
    history.csv 逐日均值线（series）；headline = |chg7d| 最大的归类。
    events 为原始英文明细（前端折叠展示）：同 commodities 能源块形状
    （label/prob/chg7d/cluster）+ slug（外链）+ question（title 提示）。
    signals 为规则引擎叙事（LLM 预留：返回 None 时不渲染）；
    unmatched = {count, samples} 收录未命中归类关键词的合约，驱动⚠补词提醒。
    只读不写盘。
    """
    snap = snapshot()
    evs = events_matching(snap, categories=("geo", "policy"))
    if not evs:
        return None
    ids = {str(m["id"]) for e in evs for m in e.get("markets") or []}
    hist = series_for(ids)
    a = str(snap.get("as_of") or "")[:10]

    by_topic: dict[str, dict] = {}
    for e in evs:  # evs 已按 24h 量降序
        key, name = geo_topic_of(e)
        mkts = sorted(
            (
                {
                    "id": str(m["id"]),
                    "label": (m.get("question") or "").removesuffix("?"),
                    "question": m.get("question"),
                    "prob": m["prob_yes"],
                    "chg7d": chg7d(hist.get(str(m["id"]))),
                    "cluster": geo_cluster_of(key, e, m)[1],
                }
                for m in active_markets(e, a)
            ),
            key=lambda x: x["prob"],
            reverse=True,
        )
        if not mkts:  # 市场全到期（如「by September 15」已过）→ 事件卡不渲染
            continue
        t = by_topic.setdefault(
            key,
            {"key": key, "name": name, "volume24hr": 0.0, "events": []},
        )
        t["volume24hr"] += e.get("volume24hr") or 0.0
        t["events"].append(
            {
                "title": e["title"],
                "slug": e.get("slug"),
                "category": e.get("category"),
                "end_date": e.get("end_date"),
                "volume24hr": e.get("volume24hr"),
                "markets": mkts,
            }
        )

    topics = sorted(by_topic.values(), key=lambda t: t["volume24hr"], reverse=True)
    # 主题内按中文归类聚合：均值概率 + 逐日均值线（history.csv 对齐取均值）。
    # 未命中关键词的市场归「其它事件」（miss 标记）并记入 unmatched：
    # 新事件会不断击穿关键词规则，必须显式提醒补 GEO_CLUSTERS 而非静默兜底。
    unmatched: list[dict] = []
    for t in topics:
        groups: dict[str, dict] = {}
        for e in t["events"]:
            for m in e["markets"]:
                g = groups.setdefault(
                    m["cluster"], {"name": m["cluster"], "markets": []}
                )
                g["markets"].append(m)
        tcls = []
        for g in groups.values():
            ms = g["markets"]
            by_date: dict[str, list[float]] = {}
            for m in ms:
                for p in hist.get(m["id"], []):
                    by_date.setdefault(p["date"], []).append(p["value"])
            series = [
                {"date": d, "value": round(sum(v) / len(v), 4)}
                for d, v in sorted(by_date.items())
            ]
            chgs = [m["chg7d"] for m in ms if m["chg7d"] is not None]
            miss = g["name"] == MISC_CLUSTER[1]
            if miss:
                unmatched.extend({"topic": t["name"], "label": m["label"]} for m in ms)
            tcls.append(
                {
                    "name": g["name"],
                    "count": len(ms),
                    "prob": round(sum(m["prob"] for m in ms) / len(ms), 4),
                    "chg7d": round(sum(chgs) / len(chgs), 1) if chgs else None,
                    "series": series,
                    "miss": miss,
                }
            )
        # 叙事/焦点取本周变动最大的归类（无变动数据时退而取概率最高）
        t["clusters"] = sorted(
            tcls,
            key=lambda c: (c["chg7d"] is None, -abs(c["chg7d"] or 0), -c["prob"]),
        )
        t["headline"] = t["clusters"][0] if t["clusters"] else None

    total_vol = sum(t["volume24hr"] for t in topics)
    total_events = sum(len(t["events"]) for t in topics)
    sig = [
        f"地缘与政治风险：{total_events} 个事件在监测（geo+policy），"
        f"24h 成交合计 ${total_vol / 1e6:.1f}M。"
    ]
    for t in topics:
        share = t["volume24hr"] / total_vol * 100 if total_vol else 0
        head = t["headline"]
        focus = (
            f"焦点归类「{head['name']}」均概率 {head['prob'] * 100:.0f}%"
            + (f"（7日 {head['chg7d']:+.1f}pp）" if head["chg7d"] is not None else "")
            if head
            else "暂无活跃市场"
        )
        sig.append(
            f"{t['name']}：{len(t['events'])} 事件 · 24h ${t['volume24hr'] / 1e6:.2f}M"
            f"（占 {share:.0f}%），{focus}。"
        )
    if unmatched:
        sample = "、".join(u["label"][:40] for u in unmatched[:3])
        sig.append(
            f"⚠ 归类规则未命中 {len(unmatched)} 个合约（如：{sample}）——"
            "新事件需要往 src/polymarket_analysis.py 的 GEO_CLUSTERS 补关键词。"
        )
    return {
        "as_of": snap.get("as_of"),
        "signals": sig,
        "topics": topics,
        "unmatched": {
            "count": len(unmatched),
            "samples": unmatched[:12],
        },
    }


def prob_ladder(event: dict) -> list[tuple[float, float]]:
    """阈值阶梯事件 → [(阈值%, 概率), …] 按阈值升序。

    适用于 "How high will X get?" 型事件（市场问题为
    "Will X reach more than 5% / at least 6.0% …"）；无阈值的问题返回空。
    """
    out: list[tuple[float, float]] = []
    for m in event.get("markets") or []:
        hit = _THRESHOLD_RX.search(m.get("question") or "")
        p = m.get("prob_yes")
        if hit and p is not None:
            out.append((float(hit.group(1)), float(p)))
    return sorted(out)
