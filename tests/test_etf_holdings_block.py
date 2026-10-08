"""assets_analysis._etf_holdings_block 单元测试（tmp_path 隔离，不依赖网络）。"""

import pandas as pd
import pytest

from src.assets_analysis import _etf_holdings_block


def _write_holdings(tmp_path, gld: list[float], slv: list[float] | None = None):
    """写 etf_holdings.csv，工作日网格，末日在 2026-10-07。"""
    idx = pd.bdate_range(end="2026-10-07", periods=len(gld))
    data = {"gld_tonnes": gld}
    if slv is not None:
        data["slv_tonnes_est"] = slv
    df = pd.DataFrame(data, index=idx)
    df.index.name = "date"
    p = tmp_path / "data" / "commodities" / "etf_holdings.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p)


class TestEtfHoldingsBlock:
    def test_flows_and_series(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        # 80 个交易日递增持仓：1日 = 末值 − 前值 = 1.0
        gld = [1000.0 + i for i in range(80)]
        slv = [15000.0 - i for i in range(80)]
        _write_holdings(tmp_path, gld, slv)
        out = _etf_holdings_block()
        assert out is not None
        assert out["dates"][-1] == "2026-10-07"
        assert len(out["gld"]["series"]) == 80
        f = out["gld"]["flows"]
        assert f["latest"] == pytest.approx(1079.0)
        assert f["d1"] == pytest.approx(1.0)
        assert f["w1"] == pytest.approx(5.0)  # 尾 5 个日间差
        assert f["m1"] == pytest.approx(21.0)
        assert f["m3"] == pytest.approx(63.0)
        assert f["latest_date"] == "2026-10-07"
        assert out["slv"]["flows"]["d1"] == pytest.approx(-1.0)

    def test_gld_only_no_slv(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_holdings(tmp_path, [1000.0 + i for i in range(10)])
        out = _etf_holdings_block()
        assert out["slv"] == {}  # 无 SLV 列 → 空对象，前端 (h.slv || {}).flows 兼容
        assert out["gld"]["flows"]["latest"] == pytest.approx(1009.0)

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        assert _etf_holdings_block() is None

    def test_short_series_no_flows(self, tmp_path, monkeypatch):
        """样本 <6 行时 flows 留空，不产出假窗口值。"""
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_holdings(tmp_path, [1000.0, 1001.0])
        out = _etf_holdings_block()
        assert out is not None
        assert out["gld"]["series"] is not None
        assert "flows" not in out["gld"]

    def test_series_window_capped_at_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_holdings(tmp_path, [1000.0 + i for i in range(600)])
        out = _etf_holdings_block()
        assert len(out["dates"]) == 500
