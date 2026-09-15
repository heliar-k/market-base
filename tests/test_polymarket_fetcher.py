"""Polymarket 监测 fetcher 单元测试（纯函数，不触网）。

样本字段摘自真实 API 响应（2026-09 gamma / clob 格式）。
"""

from src.fetchers.polymarket_fetcher import (
    build_snapshot,
    categorize,
    daily_history,
    parse_prices,
    passes_volume,
)


def _event(**kw):
    """最小事件样本（gamma 原始 shape）。"""
    base = {
        "id": "481717",
        "slug": "fed-decision-in-september-762",
        "title": "Fed Decision in September?",
        "active": True,
        "closed": False,
        "volume24hr": 18_237_871.9,
        "volume": 165_605_510.0,
        "liquidity": 13_809_055.0,
        "openInterest": 39_439_019.0,
        "negRisk": True,
        "endDate": "2026-09-16T00:00:00Z",
        "series": [{"ticker": "fomc"}],
        "markets": [
            {
                "id": "2252242",
                "question": "Will the Fed decrease rates by 25 bps?",
                "slug": "will-the-fed-25bps",
                "active": True,
                "closed": False,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.0015", "0.9985"]',
                "volume24hr": 5_195_084.7,
                "liquidityNum": 3_726_432.0,
                "endDate": "2026-09-16T00:00:00Z",
            }
        ],
    }
    base.update(kw)
    return base


def test_parse_prices_double_json():
    # 双重 JSON 字符串（API 实际格式）
    assert parse_prices('["0.0015", "0.9985"]') == [0.0015, 0.9985]
    # 已解好的 list / 空值容错
    assert parse_prices(["0.5", "0.5"]) == [0.5, 0.5]
    assert parse_prices("not json") == []
    assert parse_prices(None) == []
    assert parse_prices(["abc"]) == []


def test_categorize_series_priority():
    cat, hit = categorize(_event())
    assert (cat, hit) == ("fed", True)


def test_categorize_keyword_word_boundary():
    # confirmed 不含词边界 fed；recession 关键词命中 data
    e = _event(
        slug="will-the-deal-be-confirmed-by-march",
        title="Will the deal be confirmed by March?",
        series=[],
        volume24hr=10_000,
    )
    cat, hit = categorize(e)
    assert (cat, hit) == (None, False)  # confirmed 不含词边界 fed，无命中

    e2 = _event(
        slug="us-recession-by-end-of-2026",
        title="US recession by end of 2026?",
        series=[],
        volume24h=3_000,
        volume24hr=3_000,
    )
    cat2, _ = categorize(e2)
    assert cat2 == "data"


def test_categorize_negatives():
    # 无关事件（体育/天气）不命中任何分类
    e = _event(
        slug="broncos-vs-chiefs", title="Broncos vs. Chiefs", series=[], volume24hr=0
    )
    assert categorize(e) == (None, False)
    # confirmed 不触发 fed（词边界）
    e2 = _event(slug="x-confirmed", title="X confirmed", series=[], volume24hr=0)
    assert categorize(e2) == (None, False)


def test_categorize_geo_and_policy():
    e = _event(slug="us-x-iran-ceasefire", title="US x Iran ceasefire?", series=[])
    assert categorize(e)[0] == "geo"
    e2 = _event(
        slug="government-shutdown-october", title="Government shutdown?", series=[]
    )
    assert categorize(e2)[0] == "policy"


def test_passes_volume():
    # series 命中豁免成交量
    assert passes_volume(_event(volume24hr=0, volume=0), series_hit=True)
    # 关键词命中：24h 达标
    assert passes_volume(_event(volume24hr=6_000, volume=0), series_hit=False)
    # 关键词命中：累计达标（慢热市场）
    assert passes_volume(_event(volume24hr=0, volume=600_000), series_hit=False)
    # 都不达标
    assert not passes_volume(_event(volume24hr=3_000, volume=100_000), series_hit=False)


def test_daily_history_last_point_per_utc_day():
    pts = [
        {"t": 1788843621, "p": 0.0045},  # 2026-09-05 早
        {"t": 1788890000, "p": 0.0060},  # 2026-09-05 晚（当日最后 → 取这个）
        {"t": 1788929800, "p": 0.0050},  # 2026-09-06
    ]
    out = daily_history(pts)
    assert out["2026-09-08"] == 0.006
    assert out["2026-09-09"] == 0.005
    assert len(out) == 2


def test_build_snapshot_shape_and_sort():
    events = [
        _event(),  # fed, vol24h 18M
        _event(
            id="9",
            slug="us-recession",
            title="US recession by 2026?",
            series=[],
            negRisk=False,
            volume24hr=3_000,
            volume=1_800_000,
            markets=[
                {
                    "id": "99",
                    "question": "US recession?",
                    "slug": "rec",
                    "active": True,
                    "closed": False,
                    "outcomePrices": '["0.42", "0.58"]',
                    "volume24hr": 3_000,
                    "liquidityNum": 50_000,
                    "endDate": "2026-12-31T00:00:00Z",
                }
            ],
        ),
    ]
    snap = build_snapshot(events)
    assert snap["events"][0]["category"] == "fed"  # fed 排在 data 前
    fed = snap["events"][0]
    assert fed["series"] == "fomc"
    assert fed["end_date"] == "2026-09-16"
    m = fed["markets"][0]
    assert m["prob_yes"] == 0.0015
    # 已关闭 / 无概率的市场被剔除
    assert (
        build_snapshot([_event(markets=[dict(_event()["markets"][0], closed=True)])])[
            "events"
        ]
        == []
    )
