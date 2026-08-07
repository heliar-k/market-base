"""sec_fetcher 解析逻辑测试（fixture JSON，无网络）。"""

import gzip
import json

import pytest

from src.fetchers import sec_fetcher


def _submissions_json() -> dict:
    """模拟 submissions API：含 10-K/10-Q/10-K-A/8-K + 非 HTML 主文档。"""
    recent = {
        "accessionNumber": [
            "0000320193-25-000001",
            "0000320193-25-000002",
            "0000320193-24-000003",
            "0000320193-24-000004",
            "0000320193-24-000005",
        ],
        "filingDate": [
            "2025-11-01",
            "2025-08-01",
            "2024-11-01",
            "2024-08-01",
            "2019-05-01",
        ],
        "form": ["10-Q", "10-K", "10-K/A", "10-Q", "10-K"],
        "primaryDocument": [
            "aapl-20250927.htm",
            "aapl-20240928.htm",
            "aapl-20240928.htm",
            "aapl-20240629.xbrl",
            "aapl-20190928.htm",
        ],
        "primaryDocDescription": ["", "", "", "", ""],
    }
    return {"filings": {"recent": recent}}


def test_recent_filings_filter(monkeypatch):
    """只留 10-K/10-Q 正本、HTML 主文档、回溯期内；URL 指向 .txt 渲染。"""
    payload = json.dumps(_submissions_json()).encode()
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: payload)
    items = sec_fetcher.recent_filings("320193", years=2)
    assert [(f, d) for f, d, _ in items] == [
        ("10-K", "2025-08-01"),
        ("10-Q", "2025-11-01"),
    ]
    form, fdate, url = items[0]
    expected = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000002/aapl-20240928.htm"
    )
    assert url == expected


def test_normalize_ticker():
    assert sec_fetcher._normalize_ticker("BRK.B") == "BRKB"
    assert sec_fetcher._normalize_ticker("BRK-B") == "BRKB"
    assert sec_fetcher._normalize_ticker("005930.KS") == "005930KS"


def test_cik_map(monkeypatch):
    raw = {
        "0": {"cik_str": 320193, "ticker": "AAPL"},
        "1": {"cik_str": 1067983, "ticker": "BRK.B"},
    }
    payload = json.dumps(raw).encode()
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: payload)
    m = sec_fetcher.fetch_cik_map()
    assert m["AAPL"] == "320193"
    assert m["BRKB"] == "1067983"  # BRK-B / BRK.B 归一化后命中


def test_download_filing_extracts_text(tmp_path, monkeypatch):
    """HTML → 去 ix:header/标签纯文本 → gzip 落盘，可解压还原。"""
    body = (
        "<html><ix:header>namespace junk http://fasb.org/2025</ix:header>"
        "<body><p>" + ("10-K revenue 1000 " * 150) + "</p></body></html>"
    )
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: body.encode())
    dest = tmp_path / "10-K_2025-08-01.txt.gz"
    assert sec_fetcher._download_filing("http://example/x.htm", dest)
    assert dest.exists()
    with gzip.open(dest, "rt", encoding="utf-8") as f:
        text = f.read()
    assert "10-K revenue 1000" in text
    assert "namespace junk" not in text  # ix:header 已剥离
    assert "<html>" not in text


def test_download_filing_short_text_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: b"<html>tiny</html>")
    dest = tmp_path / "10-K_bad.txt.gz"
    assert not sec_fetcher._download_filing("http://example/x.htm", dest)
    assert not dest.exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
