"""Polymarket 预测市场监测快照。

数据源: Polymarket 官方 API（免 key、无反爬，requests 直连）：
  gamma-api.polymarket.com  — 事件/市场元数据 + 当前概率
  clob.polymarket.com       — 概率时序 prices-history（最长回溯 1 个月）

监测对象由 config.POLYMARKET_* 规则发现（series + 关键词 + 成交量地板），
不维护 slug 清单：事件结束自动退出、新事件自动进入。发现通道三条：
  1. top100 — 24h 成交量前 100 活跃事件（热点全覆盖）
  2. series — 官方系列（fomc 等，静默期也抓）
  3. search — 关键词检索（shutdown/tariff 等主题市场平时不在 top100）

输出:
  data/polymarket/{date}.json  — 当日事件快照（覆盖写，同 coinglass 模式）
  data/polymarket/history.csv  — 概率时序宽表（date 索引 × 市场 id 列，upsert）
      每日从 prices-history 回拉 1 个月小时线，取每个 UTC 日最后一个点，
      一天一值。首日即有 30 天回溯；已结束市场的列保留（历史可回看）。

用法:
  uv run python -m src.fetchers.polymarket_fetcher
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from src.config import (
    POLYMARKET_KEYWORDS,
    POLYMARKET_MIN_VOL24H,
    POLYMARKET_MIN_VOLUME,
    POLYMARKET_SERIES,
    ROOT,
)
from src.fetchers._io import upsert_timeseries

logger = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = ROOT / "data" / "polymarket"

_SLEEP = 0.2  # 秒，礼貌限速（免 key 源，全量 ~100 次调用）
_MAX_MARKETS_PER_EVENT = 12  # negRisk 事件按 24h 量取前 N 档
_MAX_HISTORY_MARKETS = 80  # 全局历史拉取上限（按 24h 量排序取前 N）

# 关键词条目 → 词边界正则（预编译；"rate cut" 这类短语整体匹配）
_KEYWORD_RES: dict[str, list[re.Pattern]] = {
    cat: [re.compile(rf"\b{re.escape(kw)}\b") for kw in kws]
    for cat, kws in POLYMARKET_KEYWORDS.items()
}


def _get(url: str, params: dict) -> list | dict:
    """GET + 礼貌限速，失败抛异常由上层按市场粒度跳过。"""
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    time.sleep(_SLEEP)
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 纯函数（解析 / 分类 / 过滤，单测覆盖）
# ═══════════════════════════════════════════════════════════════════════════════


def parse_prices(raw: str | list) -> list[float]:
    """outcomePrices 双重 JSON 字符串 → 概率列表。

    API 返回 '[\\"0.0015\\", \\"0.9985\\"]'（字符串里嵌 JSON），
    偶尔已解好的 list 也兼容。异常输入返回空列表（跳过该市场）。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    try:
        return [float(x) for x in raw]
    except (TypeError, ValueError):
        return []


def categorize(event: dict) -> tuple[str | None, bool]:
    """事件 → (分类, series 命中)。

    series 命中直接定分类；否则按 POLYMARKET_KEYWORDS 词边界匹配
    标题 + slug（'-' 归一为空格）。无命中返回 (None, False)。
    分类顺序即 config 字典序：fed > data > policy > geo > crypto。
    """
    series_tickers = {s.get("ticker") for s in (event.get("series") or [])}
    # series → 分类：fomc/fed-rate-hike/fed-rate-cut → fed；价位系列 → crypto
    series_cat = {"fomc": "fed", "fed-rate-hike": "fed", "fed-rate-cut": "fed"}
    for t in series_tickers:
        if t in series_cat:
            return series_cat[t], True
        if t and t.endswith("hit-price-monthly"):
            return "crypto", True

    text = f"{event.get('title', '')} {event.get('slug', '')}".lower().replace("-", " ")
    for cat, patterns in _KEYWORD_RES.items():
        if any(p.search(text) for p in patterns):
            return cat, False
    return None, False


def passes_volume(event: dict, series_hit: bool) -> bool:
    """成交量地板：series 命中豁免；关键词命中需 24h ≥5K 或累计 ≥500K。"""
    if series_hit:
        return True
    vol24 = float(event.get("volume24hr") or 0)
    total = float(event.get("volume") or 0)
    return vol24 >= POLYMARKET_MIN_VOL24H or total >= POLYMARKET_MIN_VOLUME


def daily_history(points: list[dict]) -> dict[str, float]:
    """prices-history 小时点 → {UTC 日期: 当日最后概率}。"""
    out: dict[str, float] = {}
    for p in sorted(points, key=lambda x: x.get("t", 0)):
        d = datetime.fromtimestamp(p["t"], tz=timezone.utc).strftime("%Y-%m-%d")
        out[d] = round(float(p["p"]), 4)
    return out


def build_snapshot(events: list[dict]) -> dict:
    """入选事件（gamma 原始 dict）→ 快照 JSON 结构。"""
    out_events = []
    for e in events:
        cat, _ = categorize(e)
        markets = []
        for m in sorted(
            e.get("markets") or [],
            key=lambda x: -(float(x.get("volume24hr") or 0)),
        ):
            if m.get("closed") or not m.get("active"):
                continue
            probs = parse_prices(m.get("outcomePrices"))
            if not probs:
                continue
            markets.append(
                {
                    "id": str(m.get("id")),
                    "question": m.get("question", ""),
                    "slug": m.get("slug", ""),
                    "prob_yes": probs[0],  # 二元市场 outcomes[0]=Yes
                    "volume24hr": round(float(m.get("volume24hr") or 0), 2),
                    "liquidity": round(float(m.get("liquidityNum") or 0), 2),
                    "end_date": (m.get("endDate") or "")[:10],
                }
            )
            if len(markets) >= _MAX_MARKETS_PER_EVENT:
                logger.info(
                    f"事件 {e.get('slug')}: 市场数超 "
                    f"{_MAX_MARKETS_PER_EVENT}，按 24h 量截断"
                )
                break
        if not markets:
            continue
        series = (e.get("series") or [{}])[0].get("ticker")
        out_events.append(
            {
                "id": str(e.get("id")),
                "slug": e.get("slug", ""),
                "title": e.get("title", ""),
                "category": cat,
                "series": series,
                "neg_risk": bool(e.get("negRisk")),
                "end_date": (e.get("endDate") or "")[:10],
                "volume24hr": round(float(e.get("volume24hr") or 0), 2),
                "volume": round(float(e.get("volume") or 0), 2),
                "liquidity": round(float(e.get("liquidity") or 0), 2),
                "open_interest": round(float(e.get("openInterest") or 0), 2),
                "icon": e.get("icon"),
                "markets": markets,
            }
        )
    # 分类序（fed → data → policy → geo → crypto）→ 24h 量 排序，页面直接按序渲染
    cat_order = {c: i for i, c in enumerate(POLYMARKET_KEYWORDS)}
    out_events.sort(key=lambda e: (cat_order.get(e["category"], 99), -e["volume24hr"]))
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": out_events,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 发现 + 拉取（网络，按事件/市场粒度容错）
# ═══════════════════════════════════════════════════════════════════════════════


def discover_events() -> dict[str, dict]:
    """三通道发现候选事件，按 event id 去重。

    top100 抓的是热点（Fed/地缘/BTC 价位）；series 抓静默期系列；
    search 抓关键词主题（shutdown/tariff 平时不在 top100）。
    """
    found: dict[str, dict] = {}

    def _add(events: list) -> None:
        for e in events:
            if e.get("closed") or not e.get("active"):
                continue
            found.setdefault(str(e["id"]), e)

    # 1. top100 by 24h volume
    _add(
        _get(
            f"{GAMMA}/events",
            {
                "closed": "false",
                "limit": 100,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
    )

    # 2. series（/series?slug= 解析 ticker → id，再抓 series_id 事件）
    for slug in POLYMARKET_SERIES:
        try:
            rows = _get(f"{GAMMA}/series", {"slug": slug})
            sid = rows[0]["id"] if rows else None
            if sid:
                _add(
                    _get(
                        f"{GAMMA}/events",
                        {"closed": "false", "series_id": sid, "limit": 10},
                    )
                )
        except Exception as exc:  # noqa: BLE001 单系列失败不拖垮整体
            logger.warning(f"series {slug} 拉取失败: {exc}")

    # 3. 关键词检索（每个关键词一次，命中太少才是常态）
    for kws in POLYMARKET_KEYWORDS.values():
        for kw in kws:
            try:
                res = _get(f"{GAMMA}/public-search", {"q": kw, "limit_per_type": 20})
                _add(res.get("events") or [])
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"search '{kw}' 失败: {exc}")

    logger.info(
        f"发现候选事件 {len(found)} 个"
        f"（top100 + {len(POLYMARKET_SERIES)} series + 关键词检索）"
    )
    return found


def _history_sort_key(item):
    """历史拉取排序：Fed Decision 事件永远排前（不被 24h 量上限挤掉），其余按量降序。"""
    m, e = item
    return (
        not str(e.get("title", "")).startswith("Fed Decision in "),
        -float(m["volume24hr"] or 0),
    )


def fetch_polymarket() -> dict:
    """主流程：发现 → 过滤 → 快照 + 概率时序 upsert。返回快照 dict。"""
    candidates = discover_events()

    selected: list[dict] = []
    for e in candidates.values():
        cat, series_hit = categorize(e)
        if cat is None:
            continue
        if not passes_volume(e, series_hit):
            continue
        selected.append(e)

    snapshot = build_snapshot(selected)
    n_markets = sum(len(e["markets"]) for e in snapshot["events"])
    cats = pd.Series([e["category"] for e in snapshot["events"]]).value_counts()
    logger.info(
        f"入选 {len(snapshot['events'])} 事件 / {n_markets} 市场，"
        f"分类分布: {cats.to_dict()}"
    )

    # ── 概率时序：全市场按 24h 量取前 _MAX_HISTORY_MARKETS 个 ──
    # Fed Decision 事件是定价页收敛路径的数据源，排最前，不被量上限挤掉
    # （2026-09 曾因此 12月 hold 市场掉出 top80 → history 断更显示为 0）
    all_markets = [(m, e) for e in snapshot["events"] for m in e["markets"]]
    all_markets.sort(key=_history_sort_key)
    if len(all_markets) > _MAX_HISTORY_MARKETS:
        logger.warning(
            f"市场数 {len(all_markets)} 超上限，"
            f"仅拉取 24h 量前 {_MAX_HISTORY_MARKETS} 个的历史"
        )
    capped = all_markets[:_MAX_HISTORY_MARKETS]

    # 需要原始 token id → 从候选事件里按市场 id 建 token 索引
    token_by_mid = {
        str(m["id"]): m.get("clobTokenIds")
        for e in selected
        for m in (e.get("markets") or [])
    }

    # 快照 prob_yes 先写入当日行（覆盖全部市场，上限外市场每日也有一个点自愈）
    history_rows: dict[str, dict[str, float]] = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for m, _e in all_markets:
        if m.get("prob_yes") is not None:
            history_rows.setdefault(today_utc, {})[m["id"]] = float(m["prob_yes"])

    for m, _ev in capped:
        tokens = token_by_mid.get(m["id"])
        if not tokens:
            continue
        try:
            tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
            res = _get(
                f"{CLOB}/prices-history",
                {
                    "market": tokens[0],
                    "interval": "1m",
                    "fidelity": 60,
                },
            )
            pts = res.get("history") or []
            for d, p in daily_history(pts).items():
                history_rows.setdefault(d, {})[m["id"]] = p
        except Exception as exc:  # noqa: BLE001 单市场失败不影响整体
            logger.warning(f"市场 {m['id']} 历史拉取失败: {exc}")

    if history_rows:
        wide = pd.DataFrame.from_dict(history_rows, orient="index")
        wide.index.name = "date"
        upsert_timeseries(DATA / "history.csv", wide)
        logger.info(f"history.csv upsert {len(wide)} 天 × {len(wide.columns)} 市场")

    # ── 快照写盘（覆盖写，同 coinglass）──
    DATA.mkdir(parents=True, exist_ok=True)
    out_path = DATA / f"{datetime.now().strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    logger.info(f"快照 → {out_path}")
    return snapshot


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    snap = fetch_polymarket()
    print(f"\n入选 {len(snap['events'])} 事件:")
    for e in snap["events"]:
        top = max(e["markets"], key=lambda m: m["prob_yes"])
        print(
            f"  [{e['category']:6s}] {e['title'][:48]:50s} "
            f"vol24h={e['volume24hr'] / 1e6:6.1f}M  {len(e['markets'])} 档  "
            f"→ 最可能: {top['question'][:60]} ({top['prob_yes']:.0%})"
        )
