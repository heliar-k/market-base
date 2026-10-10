"""assets_analysis.metals / commodities 贵金属拆分契约测试（tmp_path 隔离，无网络）。

拆分后：/api/assets/metals 带价格上下文 + ETF 持仓 + WGC 全部块；
/api/assets/commodities 只剩商品期货价/走势/20 日表，不再带贵金属资金流块。
"""

import pandas as pd

from src.assets_analysis import commodities, metals


def _write_prices(tmp_path, months: int = 8) -> None:
    idx = pd.bdate_range(end="2026-10-07", periods=months * 21)
    n = len(idx)
    df = pd.DataFrame(
        {
            "Gold": [2000.0 + i for i in range(n)],
            "Silver": [24.0 + i * 0.01 for i in range(n)],
            "WTI": [78.0 + i * 0.05 for i in range(n)],
            "NG": [2.8 + i * 0.005 for i in range(n)],
            "Copper": [3.9 + i * 0.002 for i in range(n)],
        },
        index=idx,
    )
    df.index.name = "date"
    p = tmp_path / "data" / "yfinance" / "asset_prices.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p)


def _write_pool(tmp_path, months: int = 8) -> None:
    idx = pd.bdate_range(end="2026-10-07", periods=months * 21)
    n = len(idx)
    df = pd.DataFrame(
        {
            "GLD": [240.0 + i * 0.05 for i in range(n)],
            "SLV": [27.0 + i * 0.01 for i in range(n)],
        },
        index=idx,
    )
    df.index.name = "date"
    p = tmp_path / "data" / "etf" / "pool_prices.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p)


def _write_holdings(tmp_path, gld: list[float], slv: list[float] | None = None) -> None:
    idx = pd.bdate_range(end="2026-10-07", periods=len(gld))
    data = {"gld_tonnes": gld}
    if slv is not None:
        data["slv_tonnes_est"] = slv
    df = pd.DataFrame(data, index=idx)
    df.index.name = "date"
    p = tmp_path / "data" / "commodities" / "etf_holdings.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p)


class TestMetalsPage:
    def test_metals_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_prices(tmp_path)
        _write_pool(tmp_path)
        _write_holdings(tmp_path, [1000.0 + i for i in range(80)], [15000.0 - i for i in range(80)])
        out = metals()
        # 价格上下文：期货主力（价格锚）+ ETF 行情对象
        assert [c["symbol"] for c in out["cards"]] == ["Gold", "Silver"]
        assert [c["symbol"] for c in out["etf_quotes"]] == ["GLD", "SLV"]
        assert "Gold" in out["recent"]["series"]
        # 迁入的资金流块
        assert out["etf_holdings"] is not None
        assert out["etf_holdings"]["gld"]["flows"]["latest"] == 1079.0
        assert "wgc_flows" in out and "wgc_holdings" in out and "wgc_fund_flows" in out

    def test_metals_missing_sources_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_prices(tmp_path)
        out = metals()
        # 缺数据源返回 None，不阻断主端点
        assert out["etf_holdings"] is None
        assert out["wgc_flows"] is None
        assert out["cards"][0]["symbol"] == "Gold"


class TestCommoditiesSlimmed:
    def test_no_precious_flow_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        _write_prices(tmp_path)
        _write_holdings(tmp_path, [1000.0 + i for i in range(80)])
        out = commodities()
        # 拆分后不再带贵金属资金流块（新消费方 /assets/metals 页）
        for key in ("etf_holdings", "wgc_flows", "wgc_holdings", "wgc_fund_flows"):
            assert key not in out
        # 商品三件套保留
        assert [c["symbol"] for c in out["cards"]] == [
            "Gold",
            "Silver",
            "WTI",
            "NG",
            "Copper",
        ]
        assert "normalized" in out and "recent" in out
