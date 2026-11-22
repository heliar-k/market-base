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


def test_fetch_zip_via_firecrawl_needs_key(monkeypatch):
    import pytest

    from src.fetchers import cot_fetcher as mod

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="缺 FIRECRAWL_API_KEY"):
        mod._fetch_zip_via_firecrawl("https://example.com/a.zip")


def test_fetch_zip_via_firecrawl_unwraps_base64(monkeypatch):
    """rawBase64 解出来必须是 zip（PK 头），且关键参数固定。"""
    import base64
    import io
    import zipfile

    import requests

    from src.fetchers import cot_fetcher as mod

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("f.txt", "x")
    payload = base64.b64encode(buf.getvalue()).decode()
    seen = {}

    class _Resp:
        def json(self):
            return {"success": True, "data": {"rawBase64": payload}}

        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return _Resp()

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr(requests, "post", fake_post)
    raw = mod._fetch_zip_via_firecrawl("https://example.com/a.zip")
    assert raw[:2] == b"PK"
    assert zipfile.ZipFile(io.BytesIO(raw)).namelist() == ["f.txt"]
    assert seen["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert seen["json"]["formats"] == ["rawBase64"]
    assert seen["json"]["maxAge"] == 0


def test_fetch_zip_via_firecrawl_rejects_non_zip(monkeypatch):
    """拿到 HTML 错误页（base64 后不是 PK）→ 报错，不能当 zip 硬解。"""
    import base64

    import pytest
    import requests

    from src.fetchers import cot_fetcher as mod

    blob = base64.b64encode(b"<html>403</html>").decode()

    class _Resp:
        def json(self):
            return {"success": True, "data": {"rawBase64": blob}}

        def raise_for_status(self):
            pass

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
    with pytest.raises(RuntimeError, match="不是 zip"):
        mod._fetch_zip_via_firecrawl("https://example.com/a.zip")


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
            "fin": [
                "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
                "VIX FUTURES - CBOE FUTURES EXCHANGE",
                "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
                "EURO FX - CHICAGO MERCANTILE EXCHANGE",
            ],
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
        # timsun 扩展：VIX 期货 / 美债 / 外汇
        "VX_OI",
        "VX_DEALER_L",
        "VX_DEALER_S",
        "VX_ASSET_L",
        "VX_ASSET_S",
        "VX_HEDGE_L",
        "VX_HEDGE_S",
        "ZN_OI",
        "ZN_DEALER_L",
        "ZN_DEALER_S",
        "ZN_ASSET_L",
        "ZN_ASSET_S",
        "ZN_HEDGE_L",
        "ZN_HEDGE_S",
        "EUR_OI",
        "EUR_DEALER_L",
        "EUR_DEALER_S",
        "EUR_ASSET_L",
        "EUR_ASSET_S",
        "EUR_HEDGE_L",
        "EUR_HEDGE_S",
    }
    assert len(df) == 2  # 两个报告日
    assert df.index[-1] == "2026-07-28"
    assert df.iloc[-1]["GC_OI"] == 100
    assert df.iloc[-1]["ES_OI"] == 100
    assert df.iloc[-1]["CL_MM_L"] == 10
    assert df.iloc[-1]["VX_OI"] == 100
    assert df.iloc[-1]["ZN_OI"] == 100
    assert df.iloc[-1]["EUR_OI"] == 100
