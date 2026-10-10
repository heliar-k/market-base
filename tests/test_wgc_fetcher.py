"""wgc_fetcher 单元测试（合成 xlsx + mock 浏览器通道，不依赖网络/登录态）。"""

import io

import pandas as pd
import pytest

from src.fetchers.wgc_fetcher import (
    parse_etf_flows,
    parse_fund_snapshot,
    parse_holdings,
)


def _holdings_xlsx() -> bytes:
    """模拟「Holdings by month」：前 5 列全球合计 + 每列一只基金（idx0 = ticker）。"""
    header = [
        ["ticker", "All units in tonnes", None, None, None, "gld us equity"],
        ["Active", None, None, None, None, "Active"],
        ["Fund Type", None, None, None, None, "ETF"],
        ["Region", None, None, None, None, "North America"],
        ["Country", None, None, None, None, "US"],
        ["Date", "Gold, US$/oz", "Ounces", "Tonnes", "Value (USD)", "SPDR Gold Shares"],
        ["2026-08-31", 3900, 1e8, 2900.5, 9e11, 1000.25],
        ["2026-09-30", 4000, 1e8, 2950.75, 1e12, 1002.5],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(header).to_excel(xw, sheet_name="Holdings by month", index=False, header=False)
    return buf.getvalue()


def _fund_snapshot_xlsx() -> bytes:
    """模拟「All flows by fund」：idx1 = As Of，idx2 = 表头，idx3 起数据。"""
    rows = [
        [None] * 12,
        [
            None,
            "As Of Date  30/09/2026",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
        [
            None,
            "Region",
            "Name",
            "Bloomberg ticker",
            "Country",
            "Holdings Tns",
            "Ounces",
            "AUM (US$mn)",
            "Sep 26 Demand (tonnes)",
            "Sep 26 Flows (US$mn)",
            "Q3 26 Demand (tonnes)",
            "Q3 26 Flows (US$mn)",
        ],
        [
            None,
            "North America",
            "SPDR Gold Shares",
            "gld us equity",
            "US",
            1055.3,
            3.39e7,
            141702.4,
            13.3,
            1894.5,
            50.6,
            7225.0,
        ],
        [
            None,
            None,
            "iShares Gold Trust",
            "iau us equity",
            "US",
            463.4,
            1.49e7,
            62221.0,
            3.0,
            430.4,
            -1.2,
            -53.0,
        ],
        [
            None,
            "Europe",
            "Invesco Physical Gold",
            "sgld ln equity",
            "UK",
            500.1,
            None,
            45000.0,
            None,
            1061.4,
            None,
            None,
        ],
        [None, "Total", None, None, None, 2018.8, None, None, None, None, None, None],
        [
            None,
            "GrandTotal",
            None,
            None,
            None,
            2018.8,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="All flows by fund", index=False, header=False)
    return buf.getvalue()


def _wgc_xlsx() -> bytes:
    """模拟「Fund flows by month」sheet 布局：
    前 5 列 Date/金价/Ounces/Tonnes/Value，第 6 列起每列一只基金（idx3 行 = Region）。
    """
    header = [
        # 合成 WGC 布局：5 列全球列 + 每列一只基金
        [
            "ticker",
            "All units in us$mn",
            None,
            None,
            None,
            "gld us",
            "iau us",
            "bshi cn",
        ],
        ["Active", None, None, None, None, "Active", "Active", "Active"],
        ["Fund Type", None, None, None, None, "ETF", "ETF", "ETF"],
        ["Region", None, None, None, None, "North America", "North America", "Asia"],
        ["Country", None, None, None, None, "US", "US", "CN"],
        ["Date", "Gold US$/oz", "Ounces", "Tonnes", "Value", "SPDR", "IAU", "博时"],
        ["2026-09-01", 4000, 1e8, 3000, 1e12, 3961.9, 100.0, 2255.4],
        ["2026-08-01", 3900, 1e8, 2900, 9e11, 7703.9, -50.0, 2042.2],
        ["小计/说明行", None, None, None, None, None, None, None],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(header).to_excel(
            xw, sheet_name="Fund flows by month", index=False, header=False
        )
    return buf.getvalue()


class TestParseEtfFlows:
    def test_region_sums_and_layout(self, tmp_path):
        p = tmp_path / "wgc.xlsx"
        p.write_bytes(_wgc_xlsx())
        wide = parse_etf_flows(p)
        assert list(wide.columns) == ["Asia", "North America"]
        # 2026-09：NA = 3961.9 + 100.0；Asia = 2255.4
        assert wide.loc["2026-09", "North America"] == pytest.approx(4061.9)
        assert wide.loc["2026-09", "Asia"] == pytest.approx(2255.4)
        assert wide.loc["2026-08", "North America"] == pytest.approx(7653.9)
        # 小计行（日期解析失败）被跳过
        assert len(wide) == 2

    def test_missing_region_row_raises(self, tmp_path):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([["Date", "X"], ["2026-09", 1.0]]).to_excel(
                xw, sheet_name="Fund flows by month", index=False, header=False
            )
        p = tmp_path / "bad.xlsx"
        p.write_bytes(buf.getvalue())
        with pytest.raises(RuntimeError, match="结构异常"):
            parse_etf_flows(p)

    def test_no_region_columns_raises(self, tmp_path):
        """形状够大但没有 Region 行 → 明确报错（非 IndexError）。"""
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame(
                [[f"col{j}" for j in range(10)]] + [[f"v{j}" for j in range(10)] for _ in range(8)]
            ).to_excel(xw, sheet_name="Fund flows by month", index=False, header=False)
        p = tmp_path / "bad2.xlsx"
        p.write_bytes(buf.getvalue())
        with pytest.raises(RuntimeError, match="Region"):
            parse_etf_flows(p)


class TestParseHoldings:
    def test_global_and_gld(self, tmp_path):
        p = tmp_path / "wgc.xlsx"
        p.write_bytes(_holdings_xlsx())
        df = parse_holdings(p)
        assert list(df.columns) == [
            "gold_usd_oz",
            "global_tonnes",
            "global_value_usd",
            "gld_tonnes",
        ]
        assert df.index.tolist() == ["2026-08-31", "2026-09-30"]
        assert df.loc["2026-09-30", "global_tonnes"] == pytest.approx(2950.75)
        assert df.loc["2026-09-30", "gld_tonnes"] == pytest.approx(1002.5)

    def test_missing_gld_raises(self, tmp_path):
        p = tmp_path / "wgc.xlsx"
        p.write_bytes(_holdings_xlsx())
        df = pd.read_excel(p, sheet_name="Holdings by month", header=None)
        df.iloc[0, 5] = "iau us equity"  # 改掉 ticker → 找不到 GLD 列
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.to_excel(xw, sheet_name="Holdings by month", index=False, header=False)
        with pytest.raises(RuntimeError, match="GLD"):
            parse_holdings(buf)


class TestParseFundSnapshot:
    def test_snapshot(self, tmp_path):
        p = tmp_path / "wgc.xlsx"
        p.write_bytes(_fund_snapshot_xlsx())
        snap = parse_fund_snapshot(p)
        assert snap["as_of"] == "2026-09-30"
        names = {f["name"] for f in snap["funds"]}
        assert names == {
            "SPDR Gold Shares",
            "iShares Gold Trust",
            "Invesco Physical Gold",
        }
        gld = snap["funds"][0]
        assert gld["region"] == "North America" and gld["ticker"] == "gld us equity"
        assert gld["m_flows_musd"] == pytest.approx(1894.5)
        assert gld["q_flows_musd"] == pytest.approx(7225.0)
        assert snap["funds"][2]["m_flows_musd"] == pytest.approx(1061.4)
        # Total / GrandTotal 行被跳过；Region 列空行延续上一区域
        assert len(snap["funds"]) == 3

    def test_no_funds_raises(self, tmp_path):
        # 全 None 行不扩大 xlsx used range，用占位串行撑出 12 列 × 5 行
        fill = ["x"] * 12
        rows = [
            fill,
            [
                None,
                "Region",
                "Name",
                "Bloomberg ticker",
                "Country",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ],
            [None, "Total", None, None, None, 0, None, None, None, None, None, None],
            fill,
            fill,
        ]
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame(rows).to_excel(
                xw, sheet_name="All flows by fund", index=False, header=False
            )
        with pytest.raises(RuntimeError, match="无数据行"):
            parse_fund_snapshot(buf)


class TestWgcBlock:
    def test_block_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        df = pd.DataFrame(
            {
                "North America": [100.0, 200.0],
                "Europe": [50.0, None],
                "Bogus": [1.0, 2.0],
            },
            index=pd.PeriodIndex(["2026-08", "2026-09"], freq="M").to_timestamp(),
        )
        df.index.name = "date"
        p = tmp_path / "data" / "wgc" / "etf_flows.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p)
        from src.assets_analysis import _wgc_flows_block

        b = _wgc_flows_block()
        assert b["latest_month"] == "2026-09"
        assert b["months"] == ["2026-08", "2026-09"]
        assert b["series"]["North America"] == [100.0, 200.0]
        assert "Bogus" not in b["series"]  # 非 4 区域列被过滤
        assert b["series"]["Europe"][1] is None  # NaN → null

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.assets_analysis.ROOT", tmp_path)
        from src.assets_analysis import _wgc_flows_block

        assert _wgc_flows_block() is None
