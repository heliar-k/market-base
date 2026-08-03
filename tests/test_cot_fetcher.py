"""CFTC COT fetcher 单元测试。"""

import pandas as pd

from src.fetchers.cot_fetcher import fetch_cot


def _fake_year_csv(markets: list[str]) -> str:
    """构造模拟 CFTC zip 内 CSV 文本（列名取真实文件前 20 列）。"""
    cols = [
        "Market_and_Exchange_Names",
        "Report_Date_as_YYYY-MM-DD",
        "Open_Interest_All",
        "Prod_Merc_Positions_Long_All",
        "Prod_Merc_Positions_Short_All",
        "Swap_Positions_Long_All",
        "Swap__Positions_Short_All",
        "M_Money_Positions_Long_All",
        "M_Money_Positions_Short_All",
        "Dealer_Positions_Long_All",
        "Dealer_Positions_Short_All",
        "Asset_Mgr_Positions_Long_All",
        "Asset_Mgr_Positions_Short_All",
        "Lev_Money_Positions_Long_All",
        "Lev_Money_Positions_Short_All",
        "FutOnly_or_Combined",
    ]
    rows = [",".join(cols)]
    for name in markets:
        for d, oi in (("2026-07-28", "100"), ("2026-07-21", "90")):
            vals = [f'"{name}"', d, oi] + ["10"] * 12 + ["FutOnly"]
            rows.append(",".join(vals))
    return "\n".join(rows)


def test_fetch_cot_merges_by_symbol(monkeypatch):
    """多品种按报告日横向对齐；disagg 与 fin 的指标映射各自正确。"""
    import io
    import zipfile

    def fake_download_year(year, report_type):
        markets = {
            "disagg": [
                "GOLD - COMMODITY EXCHANGE INC.",
                "CRUDE OIL, LIGHT SWEET-WTI - NEW YORK MERCANTILE EXCHANGE",
            ],
            "fin": ["E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE"],
        }[report_type]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("f.txt", _fake_year_csv(markets))
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            return pd.read_csv(zf.open(zf.namelist()[0]))

    monkeypatch.setattr("src.fetchers.cot_fetcher._download_year", fake_download_year)

    df = fetch_cot([2026])
    assert set(df.columns) == {
        "GC_OI",
        "GC_PROD_L",
        "GC_SWAP_S",
        "CL_MM_L",
        "ES_OI",
        "ES_HEDGE_L",
        "GC_PROD_S",
        "GC_SWAP_L",
        "GC_MM_L",
        "GC_MM_S",
        "CL_OI",
        "CL_PROD_L",
        "CL_PROD_S",
        "CL_SWAP_L",
        "CL_SWAP_S",
        "CL_MM_S",
        "ES_DEALER_L",
        "ES_DEALER_S",
        "ES_ASSET_L",
        "ES_ASSET_S",
        "ES_HEDGE_S",
    }
    assert len(df) == 2  # 两个报告日
    assert df.index[-1] == "2026-07-28"
    assert df.iloc[-1]["GC_OI"] == 100
    assert df.iloc[-1]["ES_OI"] == 100
    assert df.iloc[-1]["CL_MM_L"] == 10
