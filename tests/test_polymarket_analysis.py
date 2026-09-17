"""Polymarket 分析层共享读取单元测试（tmp_path 假快照，不触网不读真数据）。"""

import json

import pandas as pd
import pytest

from src.polymarket_analysis import (
    DATA,
    HIT_MISC,
    chg7d,
    crossing_series,
    crypto_hit_overview,
    events_matching,
    geo_overview,
    geo_topic_of,
    hit_cluster_of,
    hit_label,
    hit_strike,
    hit_underlying,
    prob_ladder,
    series_for,
    snapshot,
)


@pytest.fixture
def fake_dir(tmp_path, monkeypatch):
    d = tmp_path / "polymarket"
    d.mkdir()
    monkeypatch.setattr("src.polymarket_analysis.DATA", d)
    return d


def _snap(events, as_of="2026-09-16"):
    return {"as_of": as_of, "generated_at": as_of, "events": events}


def _event(title, category, vol=1000.0, markets=None, series=None):
    return {
        "id": title,
        "slug": title.lower().replace(" ", "-"),
        "title": title,
        "category": category,
        "series": series,
        "neg_risk": True,
        "end_date": "2026-12-31",
        "volume24hr": vol,
        "volume": vol * 10,
        "liquidity": vol,
        "open_interest": vol,
        "markets": markets or [],
    }


def test_snapshot_none_when_missing(fake_dir):
    assert snapshot() is None


def test_snapshot_falls_back_on_unparseable(fake_dir):
    (fake_dir / "20260917.json").write_text("{bad json", encoding="utf-8")
    (fake_dir / "20260916.json").write_text(
        json.dumps(_snap([_event("Fed x", "fed")])), encoding="utf-8"
    )
    s = snapshot()
    assert s is not None and s["as_of"] == "2026-09-16"


def test_events_matching_filters_and_sorts(fake_dir):
    snap = _snap(
        [
            _event("US recession by end of 2026?", "data", vol=100),
            _event("Fed Decision in September?", "fed", vol=900),
            _event("Strait of Hormuz traffic", "geo", vol=500, series="hormuz"),
        ]
    )
    out = events_matching(snap, categories=("data", "policy"))
    assert [e["title"] for e in out] == ["US recession by end of 2026?"]
    # 正则过滤 + volume 降序
    out = events_matching(snap, pattern=r"hormuz")
    assert len(out) == 1 and out[0]["category"] == "geo"
    # 字符串 pattern 大小写不敏感
    assert events_matching(snap, pattern="recession")[0]["category"] == "data"
    # 空快照安全
    assert events_matching(None, pattern="x") == []


def test_series_for_dedupes_dot_suffix(fake_dir):
    import pandas as pd

    df = pd.DataFrame(
        {
            "date": ["2026-09-14", "2026-09-15", "2026-09-16"],
            "111": [0.10, None, 0.30],
            "111.1": [None, 0.20, None],
            "222": [0.5, 0.6, 0.7],
        }
    )
    df.to_csv(fake_dir / "history.csv", index=False)
    out = series_for({"111"})
    # `.1` 列 bfill 归一：9-14 取 0.10、9-15 取 0.20、9-16 取 0.30
    assert out == {
        "111": [
            {"date": "2026-09-14", "value": 0.1},
            {"date": "2026-09-15", "value": 0.2},
            {"date": "2026-09-16", "value": 0.3},
        ]
    }
    assert series_for({"333"}) == {}
    assert series_for(set()) == {}


def test_chg7d_points():
    pts = [
        {"date": "2026-09-01", "value": 0.10},
        {"date": "2026-09-10", "value": 0.25},
    ]
    # 最新 9-10，7 天前 → 9-03，最近的观测是 9-01
    assert chg7d(pts) == 15.0
    assert chg7d([{"date": "2026-09-01", "value": 0.1}]) is None
    assert chg7d(None) is None


def test_prob_ladder_sorted():
    e = _event(
        "How high will inflation get in 2026?",
        "data",
        markets=[
            {
                "question": "Will inflation reach more than 5% in 2026?",
                "prob_yes": 0.095,
            },
            {
                "question": "Will inflation reach more than 4.5% in 2026?",
                "prob_yes": 0.18,
            },
            {
                "question": "Will inflation reach more than 6% in 2026?",
                "prob_yes": 0.061,
            },
            {"question": "no threshold here", "prob_yes": 0.5},
            {
                "question": "Will inflation reach at least 8.0% in 2026?",
                "prob_yes": None,
            },
        ],
    )
    assert prob_ladder(e) == [(4.5, 0.18), (5.0, 0.095), (6.0, 0.061)]
    assert prob_ladder(_event("x", "data")) == []


def test_real_data_unparsed_idempotent():
    # 真仓库若存在数据，读取不应抛异常（回归冒烟，无数据环境跳过）
    if not DATA.exists() or not list(DATA.glob("20*.json")):
        return
    s = snapshot()
    assert s is None or isinstance(s.get("events"), list)


def test_geo_topic_of_priority():
    # 热战主题优先于选举：「以色列总理选举」归以色列战线
    assert geo_topic_of(
        {"title": "Prime Minister of Israel after the next election?"}
    ) == (
        "israel",
        "以色列战线",
    )
    assert geo_topic_of({"title": "Strait of Hormuz traffic returns to normal"}) == (
        "iran",
        "伊朗与霍尔木兹",
    )
    assert geo_topic_of({"title": "Will China invade Taiwan by end of 2026?"}) == (
        "taiwan",
        "台海",
    )
    assert geo_topic_of({"title": "Russia x Ukraine ceasefire agreement by...?"}) == (
        "russia_ukraine",
        "俄乌",
    )
    assert geo_topic_of({"title": "Next French Presidential Election"}) == (
        "election",
        "选举",
    )
    assert geo_topic_of({"title": "Will the U.S. invade Greenland in 2026?"}) == (
        "other",
        "其它",
    )


def test_geo_overview(fake_dir):
    snap = _snap(
        [
            _event(
                "US x Iran Effective Ceasefire begins by...?",
                "geo",
                vol=700_000.0,
                markets=[
                    {
                        "id": "1",
                        "question": "US x Iran Effective Ceasefire by September 30?",
                        "prob_yes": 0.34,
                    },
                    # 已到期市场：概率滞留 100%，必须被过滤
                    {
                        "id": "9",
                        "question": "US x Iran Effective Ceasefire by September 4?",
                        "prob_yes": 1.0,
                        "end_date": "2026-09-04",
                    },
                ],
            ),
            _event(
                "Next French Presidential Election",
                "policy",
                vol=712_000.0,
                markets=[
                    {
                        "id": "2",
                        "question": "Will X win the French Presidential Election?",
                        "prob_yes": 0.55,
                    }
                ],
            ),
            # 全部市场到期 → 事件卡整体不渲染
            _event(
                "Strait of Hormuz traffic returns to normal by September 15?",
                "geo",
                vol=23_000.0,
                markets=[
                    {
                        "id": "3",
                        "question": "traffic normal by September 15?",
                        "prob_yes": 0.0005,
                        "end_date": "2026-09-15",
                    }
                ],
            ),
            _event(
                "Will the U.S. invade Greenland in 2026?", "geo", vol=10.0, markets=[]
            ),
        ]
    )
    (fake_dir / "20260916.json").write_text(json.dumps(snap), encoding="utf-8")
    out = geo_overview()
    assert out["as_of"] == "2026-09-16"
    # 主题按 24h 量降序：选举 712k > 伊朗 700k；过期事件与零市场事件（格陵兰）剔除
    assert [t["key"] for t in out["topics"]] == ["election", "iran"]
    iran = out["topics"][1]
    # 到期 100% 市场被过滤，焦点取活跃的 34%
    assert iran["headline"]["prob"] == 0.34
    assert len(iran["events"][0]["markets"]) == 1
    # 叙事：总览 + 每主题一句（剩 2 个有效事件）
    assert len(out["signals"]) == 3
    assert "2 个事件在监测" in out["signals"][0]


def test_geo_overview_none(fake_dir):
    # 无快照 / 无 geo 事件 → None
    assert geo_overview() is None
    (fake_dir / "20260916.json").write_text(
        json.dumps(_snap([_event("Fed Decision in September?", "fed")])),
        encoding="utf-8",
    )
    assert geo_overview() is None


def test_geo_overview_unmatched_alert(fake_dir):
    """新事件击穿关键词规则 → 归入「其它事件」兜底组并产出 ⚠ 提醒 + unmatched 清单。"""
    snap = _snap(
        [
            _event(
                "Iran foreign policy watch?",
                "geo",
                markets=[
                    {
                        "id": "1",
                        "question": "Will Iran resume missile production?",
                        "prob_yes": 0.3,
                    },
                ],
            ),
        ]
    )
    (fake_dir / "20260916.json").write_text(json.dumps(snap), encoding="utf-8")
    out = geo_overview()
    # 标题+问题均无关键词 → miss 归类 + 告警
    assert out["unmatched"]["count"] == 1
    assert (
        out["unmatched"]["samples"][0]["label"] == "Will Iran resume missile production"
    )
    iran = out["topics"][0]
    misc = [c for c in iran["clusters"] if c["miss"]]
    assert len(misc) == 1 and misc[0]["name"] == "其它事件"
    assert any("GEO_CLUSTERS" in s for s in out["signals"])
    # 正常归类的主题不产告警
    snap["events"][0]["markets"][0]["question"] = "Will Iran enrich uranium to 90%?"
    (fake_dir / "20260916.json").write_text(json.dumps(snap), encoding="utf-8")
    out2 = geo_overview()
    assert out2["unmatched"]["count"] == 0
    assert not any("GEO_CLUSTERS" in s for s in out2["signals"])


# ── 加密价位触及专题（crypto-derivatives 页）────────────────────────────


def test_hit_cluster_of():
    """归类规则：方向取问题动词，周期取 series 后缀（年度靠问题里的截止日）。"""
    month = {"series": "bitcoin-hit-price-monthly"}
    assert (
        hit_cluster_of(month, "Will Bitcoin reach $80,000 in September?") == "上行·月度"
    )
    assert (
        hit_cluster_of(month, "Will Bitcoin dip to $70,000 in September?")
        == "下行·月度"
    )
    assert (
        hit_cluster_of(month, "Will Bitcoin reach $150,000 by December 31, 2026?")
        == "上行·年度"
    )
    assert (
        hit_cluster_of({"series": "btc-multi-strikes-weekly"}, "above $76,000?")
        == "高于·短线"
    )
    assert (
        hit_cluster_of(
            {"series": "bitcoin-hit-price-daily"},
            "Will Bitcoin dip to $70,000 on September 16?",
        )
        == "下行·短线"
    )
    # 动词/系列都认不出 → 兜底组（前端虚线 chip + ⚠ 提醒补规则）
    assert (
        hit_cluster_of(
            {"series": "bitcoin-hit-price-monthly"}, "Will Bitcoin do something?"
        )
        == HIT_MISC
    )
    assert hit_underlying("ethereum-hit-price-weekly") == "ETH"
    assert hit_underlying("pepe-hit-price-monthly") == HIT_MISC
    assert hit_strike("Will Bitcoin reach $1,250 in September?") == 1250.0
    assert hit_strike("Will Bitcoin dip?") is None
    # 行内短标签：去主语 + 去句尾时间短语（多档快照的 "the price of X be" 也去掉）
    assert (
        hit_label("Will the price of Bitcoin be above $76,000 on September 17?")
        == "above $76,000"
    )
    assert (
        hit_label("Will Bitcoin dip to $25,000 by December 31, 2026?")
        == "dip to $25,000"
    )


def _hit_ev(eid, series, markets, vol24=1000.0):
    return _event(
        f"What price will {series} hit?",
        "crypto",
        vol=vol24,
        series=f"{series}-hit-price-monthly",
        markets=markets,
    )


def test_crypto_hit_overview(fake_dir):
    """标的分组 + 归类均值线 + 阶梯按价位升序 + 到期市场过滤。"""
    snap = _snap(
        [
            _hit_ev(
                "btc",
                "bitcoin",
                [
                    {
                        "id": "1",
                        "question": "Will Bitcoin reach $80,000 in September?",
                        "prob_yes": 0.46,
                    },
                    {
                        "id": "2",
                        "question": "Will Bitcoin reach $100,000 in September?",
                        "prob_yes": 0.01,
                    },
                    {
                        "id": "3",
                        "question": "Will Bitcoin dip to $70,000 in September?",
                        "prob_yes": 0.22,
                    },
                    # 已到期（日度阶梯隔日失效）→ 不进明细
                    {
                        "id": "4",
                        "question": "Will Bitcoin reach $90,000 in September?",
                        "prob_yes": 1.0,
                        "end_date": "2026-09-01",
                    },
                ],
                vol24=2000.0,
            ),
            _hit_ev(
                "eth",
                "ethereum",
                [
                    {
                        "id": "5",
                        "question": "Will Ethereum reach $2,600 in September?",
                        "prob_yes": 0.46,
                    }
                ],
            ),
            _event(
                "Fed Decision in September?",
                "fed",
                markets=[{"id": "9", "question": "?", "prob_yes": 0.5}],
            ),
        ]
    )
    (fake_dir / "20260916.json").write_text(json.dumps(snap), encoding="utf-8")
    pd.DataFrame(
        {
            "date": ["2026-09-08", "2026-09-15", "2026-09-16"],
            "1": [0.20, 0.30, 0.46],  # 上行档：7 日 +26pp
            "2": [0.05, 0.02, 0.01],
            "3": [None, 0.20, 0.22],  # 首行缺失 → 均值线从 09-15 起
        }
    ).to_csv(fake_dir / "history.csv", index=False)

    out = crypto_hit_overview()
    assert out["as_of"] == "2026-09-16"
    # 标的分组：BTC 在前（24h 量大），fed 事件不出现
    assert [t["key"] for t in out["topics"]] == ["BTC", "ETH"]
    btc = out["topics"][0]
    assert btc["volume24hr"] == 2000.0 and btc["volume"] == 20000.0
    # 归类：上行两档均值 (0.46+0.01)/2，下行单档；|7日变动| 大的在前
    assert [c["name"] for c in btc["clusters"]] == ["上行·月度", "下行·月度"]
    up = btc["clusters"][0]
    assert up["count"] == 2 and up["prob"] == round((0.46 + 0.01) / 2, 4)
    assert up["chg7d"] == 11.0  # (26.0 + -4.0) / 2
    # 归类均值线按日期对齐（09-08 只有上行两档，09-15 起三档都在）
    assert [p["date"] for p in up["series"]] == [
        "2026-09-08",
        "2026-09-15",
        "2026-09-16",
    ]
    assert up["series"][0]["value"] == 0.125
    # 归类级「中位触及档」：概率最接近 50% 的那一档（$80,000 46.5%）；无对赌档则 None
    assert up["pivot"] == {"strike": 80000.0, "prob": 0.46}
    assert [c for c in btc["clusters"] if c["name"] == "下行·月度"][0]["pivot"] is None
    assert btc["headline"] is up
    # 明细：按价位升序（不是按概率），到期市场已剔除
    ev = btc["events"][0]
    assert [m["strike"] for m in ev["markets"]] == [70000.0, 80000.0, 100000.0]
    assert [m["label"] for m in ev["markets"]] == [
        "dip to $70,000",
        "reach $80,000",
        "reach $100,000",
    ]
    # 叙事：总览一句 + 每标的一句
    assert len(out["signals"]) == 3 and out["unmatched"]["count"] == 0
    assert "上行·月度" in out["signals"][1]


def test_crypto_hit_overview_unmatched(fake_dir):
    """问题动词认不出 → 虚线「其它」归类 + ⚠ 补规则叙事。"""
    snap = _snap(
        [
            _hit_ev(
                "btc",
                "bitcoin",
                [
                    {
                        "id": "1",
                        "question": "Will Bitcoin print a new high?",
                        "prob_yes": 0.3,
                    }
                ],
            )
        ]
    )
    (fake_dir / "20260916.json").write_text(json.dumps(snap), encoding="utf-8")
    out = crypto_hit_overview()
    assert out["unmatched"]["count"] == 1
    assert out["topics"][0]["clusters"][0]["miss"] is True
    assert any("hit_cluster_of" in s for s in out["signals"])


def test_crypto_hit_overview_none(fake_dir):
    """无快照 / 无 crypto 事件 → None（坏 JSON 回退前一日）。"""
    assert crypto_hit_overview() is None
    (fake_dir / "20260916.json").write_text(
        json.dumps(_snap([_event("Fed Decision?", "fed")])), encoding="utf-8"
    )
    assert crypto_hit_overview() is None
    (fake_dir / "20260917.json").write_text("{bad json", encoding="utf-8")
    (fake_dir / "20260916.json").write_text(
        json.dumps(
            _snap(
                [
                    _hit_ev(
                        "btc",
                        "bitcoin",
                        [
                            {
                                "id": "1",
                                "question": "Will Bitcoin reach $80,000 in September?",
                                "prob_yes": 0.4,
                            }
                        ],
                    )
                ]
            )
        ),
        encoding="utf-8",
    )
    assert crypto_hit_overview()["as_of"] == "2026-09-16"


def test_crossing_series():
    """过 50% 档 = 相邻两档线性插值；整条阶梯在同侧或档位不足则留空（不给假锚点）。"""
    ms = [
        {"id": "a", "strike": 70000.0, "prob": 0.9},
        {"id": "b", "strike": 80000.0, "prob": 0.6},
        {"id": "c", "strike": 90000.0, "prob": 0.4},
        {"id": "d", "strike": 100000.0, "prob": 0.1},
    ]
    hist = {m["id"]: [{"date": "2026-09-16", "value": m["prob"]}] for m in ms}
    # 0.5 落在 80K(0.6) 与 90K(0.4) 正中 → 85K
    assert crossing_series(ms, hist, rising=False) == [
        {"date": "2026-09-16", "value": 85000.0}
    ]
    # 全低于 0.5（年内上行那种无对赌档）→ 空
    low = {
        m["id"]: [{"date": "2026-09-16", "value": v}]
        for m, v in zip(ms, (0.2, 0.15, 0.1, 0.05))
    }
    assert crossing_series(ms, low, rising=False) == []
    # 下行档概率随价位递增：0.5 落在 80K(0.3) 与 90K(0.55) 间 → 88K
    rising = {
        m["id"]: [{"date": "2026-09-16", "value": v}]
        for m, v in zip(ms, (0.1, 0.3, 0.55, 0.8))
    }
    assert crossing_series(ms, rising, rising=True) == [
        {"date": "2026-09-16", "value": 88000.0}
    ]
    # 当日只有两档 → 不足以定跨档位置，留空
    two = {m["id"]: hist[m["id"]] for m in ms[:2]}
    assert crossing_series(ms[:2], two, rising=False) == []
