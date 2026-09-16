"""Polymarket 分析层共享读取单元测试（tmp_path 假快照，不触网不读真数据）。"""

import json

import pytest

from src.polymarket_analysis import (
    DATA,
    chg7d,
    events_matching,
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
    assert snapshot(categories=("fed",)) is None


def test_snapshot_falls_back_on_unparseable(fake_dir):
    (fake_dir / "20260917.json").write_text("{bad json", encoding="utf-8")
    (fake_dir / "20260916.json").write_text(
        json.dumps(_snap([_event("Fed x", "fed")])), encoding="utf-8"
    )
    s = snapshot(categories=("fed",))
    assert s is not None and s["as_of"] == "2026-09-16"


def test_snapshot_category_filter_falls_back_days(fake_dir):
    # 最新一日只有 geo，无 fed → 回退前一日取含 fed 的
    (fake_dir / "20260917.json").write_text(
        json.dumps(_snap([_event("Hormuz", "geo")], as_of="2026-09-17")),
        encoding="utf-8",
    )
    (fake_dir / "20260916.json").write_text(
        json.dumps(_snap([_event("Fed Decision in September?", "fed")])),
        encoding="utf-8",
    )
    s = snapshot(categories=("fed",))
    assert s["as_of"] == "2026-09-16"
    # 不过滤分类则取最新
    assert snapshot()["as_of"] == "2026-09-17"


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
    # 正则 + series 过滤，volume 降序
    out = events_matching(snap, pattern=r"hormuz", series=("hormuz",))
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
