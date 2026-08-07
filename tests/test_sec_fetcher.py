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
            "0001046179-25-000006",
            "0001046179-25-000007",
        ],
        "filingDate": [
            "2025-11-01",
            "2025-08-01",
            "2024-11-01",
            "2024-08-01",
            "2019-05-01",
            "2025-05-01",
            "2025-04-17",
        ],
        "form": ["10-Q", "10-K", "10-K/A", "10-Q", "10-K", "20-F", "6-K"],
        "primaryDocument": [
            "aapl-20250927.htm",
            "aapl-20240928.htm",
            "aapl-20240928.htm",
            "aapl-20240629.xbrl",
            "aapl-20190928.htm",
            "tsmc-20250430.htm",
            "tsm-20250417x6k.htm",
        ],
        "primaryDocDescription": ["", "", "", "", "", "", ""],
    }
    return {"filings": {"recent": recent}}


def test_recent_filings_filter(monkeypatch):
    """只留 10-K/10-Q/20-F 正本、HTML 主文档、回溯期内；URL 指向无破折号直链。"""
    payload = json.dumps(_submissions_json()).encode()
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: payload)
    items = sec_fetcher.recent_filings("320193", years=2)
    assert [(f, d) for f, d, _ in items] == [
        ("20-F", "2025-05-01"),
        ("10-K", "2025-08-01"),
        ("10-Q", "2025-11-01"),
    ]
    form, fdate, url = items[1]
    expected = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000002/aapl-20240928.htm"
    )
    assert url == expected


def test_6k_full_text_url_and_pattern(monkeypatch):
    """6-K 走完整提交文本直链（目录无破折号、文件名带破折号），
    doc_pattern 只放行匹配主文档名的 6-K。"""
    payload = json.dumps(_submissions_json()).encode()
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: payload)
    items = sec_fetcher.recent_filings(
        "1046179",
        years=2,
        forms=("6-K",),
        doc_pattern=r"^tsm-\d{8}x6k",
    )
    assert [(f, d) for f, d, _ in items] == [("6-K", "2025-04-17")]
    form, fdate, url = items[0]
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1046179/"
        "000104617925000007/0001046179-25-000007.txt"
    )


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


def test_download_full_text_strips_sec_meta(tmp_path, monkeypatch):
    """完整提交文本：剥 <SEC-HEADER>/<SEC-DOCUMENT> 元数据后落盘。"""
    body = (
        b"<SEC-DOCUMENT>0001.txt : 20250417\n"
        b"<SEC-HEADER>ACCESSION NUMBER: 0001</SEC-HEADER>\n"
        b"<DOCUMENT><TYPE>6-K</TYPE><TEXT><html><p>"
        + b"revenue 1000 " * 150
        + b"</p></html></TEXT>"
    )
    monkeypatch.setattr(sec_fetcher, "_get", lambda url: body)
    dest = tmp_path / "6-K_2025-04-17_0001.txt.gz"
    assert sec_fetcher._download_filing("http://example/0001.txt", dest)
    with gzip.open(dest, "rt", encoding="utf-8") as f:
        text = f.read()
    assert "revenue 1000" in text
    assert "SEC-HEADER" not in text
    assert "<html>" not in text  # 附件 HTML 一并剥离
    assert "ACCESSION" not in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
