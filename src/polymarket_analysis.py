"""Polymarket 分析层共享读取（供 daily_brief / 专题分析 / geo 页复用）。

职责：读 data/polymarket/ 最新快照 + history.csv 概率时序，提供事件过滤与
7 日变化。不做聚类叙事（那部分在 geo_overview 等"给人看"的入口里）。

fed_analysis.market_odds 与 assets_analysis._polymarket 维护各自的读取路径
（前者含 `.1` 后缀 bfill 归一），本模块是新消费方的统一入口，不回收旧代码。

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


def snapshot(categories: tuple[str, ...] | None = None) -> dict | None:
    """最新可解析且含指定分类事件的快照（坏 JSON / 无目标事件回退前一日）。

    返回原样 dict（含 events / as_of）；无文件或全不匹配返回 None。
    categories=None 不做分类过滤。
    """
    for f in sorted(DATA.glob("20*.json"), reverse=True):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if categories is None:
            return snap
        if any(e.get("category") in categories for e in snap.get("events") or []):
            return snap
    return None


def events_matching(
    snap: dict | None,
    pattern: str | re.Pattern | None = None,
    categories: tuple[str, ...] = (),
    series: tuple[str, ...] = (),
) -> list[dict]:
    """快照事件过滤：分类 + series + 标题正则（不区分大小写）。

    按事件 volume 降序返回。pattern 是纯字符串时按词不敏感包含处理
    （调用方传已编译正则可控制词边界）。
    """
    if not snap:
        return []
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    out = []
    for e in snap.get("events") or []:
        if categories and e.get("category") not in categories:
            continue
        if series and (e.get("series") or "") not in series:
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

    结构：{as_of, signals, topics: [{key, name, volume24hr, headline, events}]}。
    事件卡同 commodities 能源块形状（label/prob/chg7d）+ slug（外链）
    + question（title 提示）；焦点 headline = 主题内 24h 量最大事件的最高概率市场。
    signals 为规则引擎叙事（LLM 预留：返回 None 时不渲染）。只读不写盘。
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
    history: dict[str, list[dict]] = {}
    for t in topics:
        top = t["events"][0]
        t["headline"] = (
            {"title": top["title"], **top["markets"][0]} if top["markets"] else None
        )
        hid = t["headline"].get("id") if t["headline"] else None
        if hid and hid in hist:
            history[hid] = hist[hid]  # 焦点走势图（每主题一条，控制体积）

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
            f"焦点「{head['label']}」{head['prob'] * 100:.0f}%"
            if head
            else "暂无活跃市场"
        )
        sig.append(
            f"{t['name']}：{len(t['events'])} 事件 · 24h ${t['volume24hr'] / 1e6:.2f}M"
            f"（占 {share:.0f}%），{focus}。"
        )
    return {
        "as_of": snap.get("as_of"),
        "signals": sig,
        "topics": topics,
        "history": history,
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
