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
