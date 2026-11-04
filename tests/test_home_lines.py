"""首屏「今日一句话」结论层（issue #21）：导出字段 + 前端消费两端各锁一刀。

形状：`/api/home-lines` → {generator, lines:[{key,label,text,as_of,trigger?}]}，key 必须是
SITE_NAV 的组键/子页键（前端靠它解出专题页路径，不硬编码路由）；`trigger` 是可选的
专题内生触发短签（今日头版卡片右上角的徽章，写的是「哪个数把这张卡标热」）。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.server import (
    _fed_home,
    _geo_home,
    _inflation_trigger,
    _labor_trigger,
    _vol_home,
    get_home_lines,
)

ROOT = Path(__file__).resolve().parent.parent

# 10 个专题引擎；少一行就是某个引擎的叙事字段改名/断供了（红的时候看 skip 日志）
EXPECTED_KEYS = {
    "rates",
    "credit",
    "inflation",
    "labor",
    "treasury",
    "vol",
    "liquidity",
    "assets",
    "fed",
    "geo",
}

# 触发签字符预算：卡片右上角 275px 档可用约 118px（约 10 个汉字），控制在短口径内。
TRIGGER_BUDGET = 16


def _geo_topic(key: str, name: str, chg7d: float, prob: float = 0.5) -> dict:
    return {
        "key": key,
        "name": name,
        "volume24hr": 1_620_000.0,
        "events": [{}, {}],
        "headline": {"name": "停火与军事行动", "chg7d": chg7d, "prob": prob},
    }


def test_home_lines_covers_all_engines() -> None:
    out = get_home_lines()
    lines = out["lines"]
    assert out["generator"] == "rules"
    assert {x["key"] for x in lines} == EXPECTED_KEYS, (
        "有专题引擎没出一句话（看导出日志里的 home-lines skip）："
        f"{sorted(EXPECTED_KEYS - {x['key'] for x in lines})}"
    )
    nav = (ROOT / "frontend/public/js/site-nav.js").read_text(encoding="utf-8")
    nav_keys = set(re.findall(r"key: '([^']+)'", nav))
    for line in lines:
        assert line["key"] and line["label"] and line["text"], line
        assert line["key"] in nav_keys, f"key 解不出专题页：{line['key']}"
        # 首句不带句末句号（行尾还有观测日，读起来才像一行结论）
        assert not line["text"].endswith("。"), line
        assert line["as_of"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", line["as_of"]), line
        # 触发签要么不出现，要么是能塞进卡片徽章的短串
        trigger = line.get("trigger")
        if trigger is not None:
            assert 0 < len(trigger) <= TRIGGER_BUDGET, trigger


class TestIndicatorsTrigger:
    """通胀 / 就业 / 联储：触发签只在指标本身越过门槛时出现。"""

    def test_inflation_fires_above_target_gap(self):
        assert _inflation_trigger({"cards": {"core_pce": {"value": 3.12}}}) == "核心 PCE 3.12%"

    def test_inflation_quiet_below_threshold(self):
        assert _inflation_trigger({"cards": {"core_pce": {"value": 2.4}}}) is None

    def test_inflation_missing_data(self):
        assert _inflation_trigger({}) is None
        assert _inflation_trigger({"cards": {"core_pce": {"value": None}}}) is None

    def test_labor_sahm_first(self):
        cards = {"unrate": {"sahm": 0.6, "chg_3m": 0.4}, "nfp": {"value": 20.0}}
        assert _labor_trigger({"cards": cards}) == "Sahm 0.60"

    def test_labor_unrate_3m_change(self):
        cards = {"unrate": {"sahm": 0.1, "chg_3m": 0.3}, "nfp": {"value": 20.0}}
        assert _labor_trigger({"cards": cards}) == "失业率 3M +0.3pp"

    def test_labor_weak_payrolls(self):
        cards = {"unrate": {"sahm": 0.0, "chg_3m": 0.0}, "nfp": {"value": 29.0}}
        assert _labor_trigger({"cards": cards}) == "非农 29K"

    def test_labor_quiet(self):
        cards = {"unrate": {"sahm": 0.0, "chg_3m": 0.0}, "nfp": {"value": 180.0}}
        assert _labor_trigger({"cards": cards}) is None

    def test_fed_fires_only_when_off_neutral(self):
        # 0.4 落在 stance_label 的中性档（-0.5~0.5）内，不该给中性口径挂触发签
        mid = {"label": "中性", "score": 0.4, "sample": 20}
        text, _, trigger = _fed_home({"indicator": mid, "statements": [], "speeches": []})
        assert trigger is None
        assert "中性" in text
        hawk = {"label": "鹰派", "score": 2.0, "sample": 20}
        fed = _fed_home({"indicator": hawk, "statements": [], "speeches": []})
        assert fed[2] == "联储 鹰派 (+2.0)"


class TestVolTrigger:
    """波动率：全景仪表盘的定调 + 信号卡标题，触发签取最靠前的越界指标。"""

    def _d(self, indices: list[dict], signals: list[dict] | None = None) -> dict:
        return {
            "as_of": "2026-10-30",
            "as_of_snapshot": "2026-11-01",
            "hero": {"verdict": "权益波动处于正常区间，跨资产分化中需关注利率与商品端的溢出。"},
            "signals": signals if signals is not None else [],
            "indices": indices,
        }

    def test_text_joins_verdict_and_signal_titles(self):
        d = self._d(
            [{"symbol": "VIX", "value": 17.2}],
            [{"title": "利率波动中性"}, {"title": "商品波动仍是主线"}, {"title": ""}],
        )
        text, as_of, trigger = _vol_home(d)
        assert text == (
            "权益波动处于正常区间，跨资产分化中需关注利率与商品端的溢出"
            " —— 利率波动中性 · 商品波动仍是主线"
        )
        assert as_of == "2026-11-01"
        assert trigger is None

    def test_trigger_vix_alert(self):
        d = self._d([{"symbol": "VIX", "value": 25.4}, {"symbol": "VVIX", "value": 110.0}])
        assert _vol_home(d)[2] == "VIX 25.4"

    def test_trigger_vvix_behind_quiet_vix(self):
        d = self._d([{"symbol": "VIX", "value": 18.0}, {"symbol": "VVIX", "value": 110.4}])
        assert _vol_home(d)[2] == "VVIX 110"

    def test_trigger_ovx_double_vix(self):
        d = self._d([{"symbol": "VIX", "value": 17.0}, {"symbol": "OVX", "value": 41.6}])
        assert _vol_home(d)[2] == "OVX 42"

    def test_quiet_returns_no_trigger(self):
        d = self._d([{"symbol": "VIX", "value": 14.2}, {"symbol": "VVIX", "value": 82.0}])
        assert _vol_home(d)[2] is None


class TestGeoTopic:
    """地缘：有 ≥5pp 异动时报道异动最大的主题，触发签用短名 + 整数 pp。"""

    def _d(self, topics: list[dict], signals: list[str] | None = None) -> dict:
        return {
            "as_of": "2026-11-01",
            "signals": signals if signals is not None else ["首句", "次句"],
            "topics": topics,
        }

    def test_trigger_short_name_and_integer_pp(self):
        d = self._d([_geo_topic("iran", "伊朗与霍尔木兹", 13.8)])
        text, as_of, trigger = _geo_home(d)
        assert trigger == "霍尔木兹 7d +14pp"
        # 正文用全名，徽章才用短名
        assert text.startswith("伊朗与霍尔木兹：2 事件")
        assert as_of == "2026-11-01"

    def test_trigger_picks_largest_absolute_move(self):
        d = self._d(
            [
                _geo_topic("israel", "以色列战线", -6.2),
                _geo_topic("iran", "伊朗与霍尔木兹", 13.8),
                _geo_topic("taiwan", "台海", 5.1),
            ]
        )
        assert _geo_home(d)[2] == "霍尔木兹 7d +14pp"

    def test_quiet_below_threshold_falls_back_to_second_signal(self):
        d = self._d([_geo_topic("iran", "伊朗与霍尔木兹", 3.0)])
        text, _, trigger = _geo_home(d)
        assert trigger is None
        assert text == "次句"

    def test_unknown_topic_key_falls_back_to_full_name(self):
        d = self._d([_geo_topic("other", "其它", 6.0)])
        assert _geo_home(d)[2] == "其它 7d +6pp"


def test_daily_consumes_home_lines() -> None:
    """今日头版（/daily/，ADR-0005 后 home-lines 唯一消费方）必须拉 /api/home-lines。

    原 test_dashboard_consumes_home_lines 随 SPA 仪表盘视图删除而改写：
    dashboard.js 的 NAV_PAGES/SITE_NAV 派生守卫同批废弃，专题路径改在
    test_static_single_source 的字面量链接守卫下覆盖。
    """
    js = (ROOT / "frontend/src/pages/daily/index.astro").read_text(encoding="utf-8")
    assert "/api/home-lines" in js, "今日头版未接入 /api/home-lines"
    assert "LINE_LINKS" in js and "R.setAsOf(" in js, "缺 LINE_LINKS 映射或 R.setAsOf 时效组装"
