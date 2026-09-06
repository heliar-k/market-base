"""daily_brief 规则引擎测试（指标行 / 三情景规则匹配 / 变化方向）。"""

import pandas as pd

from src import daily_brief as db


def _mk_series(vals: list[float], start: str = "2026-01-05") -> pd.Series:
    idx = pd.date_range(start, periods=len(vals), freq="D")
    return pd.Series(vals, index=idx, dtype=float)


class TestIndicators:
    def test_chg_px_pct(self):
        s = _mk_series([100, 100, 100, 100, 100, 100, 102])
        assert db._chg(s, 5, "px") == 2.0  # 最新 vs 5 观测前

    def test_chg_bp_diff(self):
        s = _mk_series([4.70, 4.70, 4.70, 4.70, 4.70, 4.70, 4.78])
        assert db._chg(s, 5, "bp") == 0.08

    def test_chg_insufficient(self):
        assert db._chg(_mk_series([1, 2]), 5, "px") is None

    def test_spark_n(self):
        s = _mk_series([float(i) for i in range(30)])
        assert len(db._spark(s)) == db.SPARK_N

    def test_indicators_missing_series(self):
        d = db._indicators({})  # 全缺失 → 行保留但不带数值
        assert len(d["rows"]) == len(db.INDICATORS)
        assert d["rows"][0].get("last") is None


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
        assert len(out["indicators"]["rows"]) == len(db.INDICATORS)
        assert len(out["scenarios"]) == 3
        assert all("matched" in s for s in out["scenarios"])
