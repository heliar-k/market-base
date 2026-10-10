"""etf_holdings_fetcher 单元测试（mock HTTP，不依赖网络）。"""

import io

import pandas as pd
import pytest

from src.fetchers import etf_holdings_fetcher as mod


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.text = content.decode("utf-8", errors="ignore")
        self.headers = {"content-type": "application/octet-stream"}
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _gld_xlsx() -> bytes:
    """构造 GLD 官方档案同结构的最小 xlsx（含 US Holiday 脏行）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Disclaimer Sheet"
    ws = wb.create_sheet("US GLD Historical Archive")
    ws.append(["Date", "Tonnes of Gold"])
    ws.append(["18-Nov-2004", 8.09])
    ws.append(["US Holiday", "US Holiday"])  # 实际文件里假日行的两列都是文本
    ws.append(["19-Nov-2004", 57.85])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _slv_xml() -> bytes:
    """构造 SLV varnish 文档同结构的最小 SpreadsheetML（Historical sheet）。"""

    def row(cells):
        return (
            "<ss:Row>"
            + "".join(f'<ss:Cell><ss:Data ss:Type="String">{c}</ss:Data></ss:Cell>' for c in cells)
            + "</ss:Row>"
        )

    return (
        '<ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<ss:Worksheet ss:Name="Disclaimers"><ss:Table>'
        + row(["This file is for information purposes only"])
        + "</ss:Table></ss:Worksheet>"
        '<ss:Worksheet ss:Name="Historical"><ss:Table>'
        + row(["As Of", "NAV per Share", "Ex-Dividends", "Shares Outstanding"])
        + row(["Oct 07, 2026", "54.25", "--", "545,100,000"])
        + row(["Oct 06, 2026", "55.08", "--", "545,800,000"])
        + row(["bad row", "x", "--", "not-a-number"])  # 解析失败行应被跳过
        + "</ss:Table></ss:Worksheet>"
        '<ss:Worksheet ss:Name="Performance"><ss:Table>'
        + row(["NAV", "100"])  # 非 Historical sheet，不应混入
        + "</ss:Table></ss:Worksheet>"
        "</ss:Workbook>"
    ).encode()


class TestFetchGld:
    def test_parse_and_drop_holiday(self, monkeypatch):
        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _Resp(_gld_xlsx()))
        df = mod.fetch_gld()
        assert list(df.columns) == ["gld_tonnes"]
        assert len(df) == 2  # holiday 行被丢弃
        assert df.index[0] == pd.Timestamp("2004-11-18")
        assert df["gld_tonnes"].iloc[0] == pytest.approx(8.09)

    def test_non_xlsx_rejected(self, monkeypatch):
        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _Resp(b"<html>blocked</html>"))
        with pytest.raises(RuntimeError, match="非 xlsx"):
            mod.fetch_gld()


class TestFetchSlv:
    def test_parse_shares_and_tonnes(self, monkeypatch):
        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _Resp(_slv_xml()))
        monkeypatch.setattr(mod, "_slv_oz_per_share", lambda shares: 0.9)
        df = mod.fetch_slv()
        assert list(df.columns) == ["slv_shares_mn", "slv_tonnes_est"]
        assert len(df) == 2  # 表头行 + 脏行 + Performance sheet 都被跳过
        assert df.index[-1] == pd.Timestamp("2026-10-07")
        # 545.1M 股 × 0.9 oz ÷ 32150.7465 ≈ 15259.1 t
        assert df["slv_tonnes_est"].iloc[-1] == pytest.approx(15259.1, abs=0.5)
        assert df["slv_shares_mn"].iloc[-1] == pytest.approx(545.1)

    def test_missing_sheet_rejected(self, monkeypatch):
        monkeypatch.setattr(
            mod.requests,
            "get",
            lambda *a, **kw: _Resp(b'<ss:Workbook xmlns:ss="x"></ss:Workbook>'),
        )
        with pytest.raises(RuntimeError, match="Historical"):
            mod.fetch_slv()


class TestSlvOzPerShare:
    def test_extract_from_page_json(self, monkeypatch):
        page = (
            "x&quot;formattedValue&quot;:&quot;15,311.57&quot;,&quot;sortOrder&quot;:46,"
            "&quot;name&quot;:&quot;tonnes&quot;}y"
        ).encode()
        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _Resp(page))
        # 15311.57 t × 32150.7465 oz/t ÷ 545.1M 股 ≈ 0.9034 oz/股
        assert mod._slv_oz_per_share(545.1e6) == pytest.approx(0.9034, abs=1e-3)

    def test_fallback_on_garbage(self, monkeypatch):
        def _boom(*a, **kw):
            raise ConnectionError("network down")

        monkeypatch.setattr(mod.requests, "get", _boom)
        assert mod._slv_oz_per_share(545.1e6) == mod.SLV_OZ_PER_SHARE_FALLBACK


class TestRun:
    def test_partial_failure_upserts_survivor(self, monkeypatch, tmp_path):
        out = tmp_path / "etf_holdings.csv"
        monkeypatch.setattr(mod, "_OUT", out)
        monkeypatch.setattr(
            mod,
            "fetch_gld",
            lambda: pd.DataFrame(
                {"gld_tonnes": [100.0, 101.0]},
                index=pd.to_datetime(["2026-10-06", "2026-10-07"]),
            ),
        )

        def _slv_fail():
            raise ConnectionError("SLV down")

        monkeypatch.setattr(mod, "fetch_slv", _slv_fail)
        df = mod.run()
        assert "gld_tonnes" in df.columns
        assert out.exists()
        assert pd.read_csv(out)["gld_tonnes"].notna().all()

    def test_all_fail_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "_OUT", tmp_path / "etf_holdings.csv")

        def _fail():
            raise ConnectionError("down")

        monkeypatch.setattr(mod, "fetch_gld", _fail)
        monkeypatch.setattr(mod, "fetch_slv", _fail)
        with pytest.raises(RuntimeError, match="全部拉取失败"):
            mod.run()
