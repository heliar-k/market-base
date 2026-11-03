"""daily_brief 规则引擎测试（时效分组 / 三情景规则匹配 / 变化方向）。"""

import pandas as pd

from src import daily_brief as db


def _mk_series(vals: list[float], start: str = "2026-01-05") -> pd.Series:
    idx = pd.date_range(start, periods=len(vals), freq="D")
    return pd.Series(vals, index=idx, dtype=float)


class TestIndicators:
    def test_indicators_missing_series(self):
        d = db._indicators({})
        assert d["as_of"] is None
        assert d["groups"] == {}

    def test_indicators_groups_and_as_of(self):
        s = {
            "SPX": _mk_series([100.0, 101.0], start="2026-01-05"),
            "Y10": _mk_series([4.0, 4.1], start="2026-01-07"),
        }
        d = db._indicators(s)
        assert d["as_of"] == "2026-01-08"
        assert d["groups"]["资产"] == "2026-01-06"
        assert d["groups"]["利率/利差"] == "2026-01-08"
        assert "流动性" not in d["groups"]


class TestScenarios:
    def _base(self) -> dict[str, pd.Series]:
        up = [100 + i for i in range(7)]  # 7 天上行
        down = [100 - i for i in range(7)]
        return {
            "SPX": _mk_series(up),
            "HY_OAS": _mk_series(down),
            "VIX": _mk_series(down),
            "WTI": _mk_series(up),
            "Y10": _mk_series(up),
        }

    def test_risk_on_hit(self):
        sc = {s["key"]: s for s in db._scenarios(self._base())}
        assert sc["risk-on"]["matched"] is True
        assert sc["risk-off"]["matched"] is False

    def test_risk_off_hit(self):
        series = self._base()
        series["SPX"] = _mk_series([100 - i for i in range(7)])  # 股跌
        series["HY_OAS"] = _mk_series([100 + i for i in range(7)])  # 利差走扩
        sc = {s["key"]: s for s in db._scenarios(series)}
        assert sc["risk-off"]["matched"] is True

    def test_energy_hit(self):
        series = self._base()
        series["SPX"] = _mk_series([100 - i for i in range(7)])
        sc = {s["key"]: s for s in db._scenarios(series)}  # 油涨 + 股跌 + 10Y 上行
        assert sc["energy-cost"]["matched"] is True

    def test_common_window(self):
        series = self._base()
        # SPX 起点更晚但与 HY 有共同覆盖 → 起点取最晚、终点取最早
        series["SPX"] = _mk_series([100.0] * 6, start="2026-01-09")
        w = db._window(series, ["SPX", "HY_OAS"])
        assert w == ("2026-01-09", "2026-01-11")

    def test_window_no_overlap(self):
        series = self._base()
        series["SPX"] = _mk_series([100.0] * 6, start="2026-01-20")  # 与 HY 不重叠
        assert db._window(series, ["SPX", "HY_OAS"]) is None

    def test_window_insufficient(self):
        assert db._window({"SPX": _mk_series([1.0])}, ["SPX"]) is None

    def test_dir_flat(self):
        assert db._dir(_mk_series([5.0] * 8)) == 0


class TestEntry:
    def test_generate_runs_on_real_data(self):
        out = db.generate_daily_brief()
        assert out["generator"] == "rules"
        assert "groups" in out["indicators"]
        assert len(out["scenarios"]) == 3
        assert all("matched" in s for s in out["scenarios"])
