"""ACM fetcher 单元测试：CSV 解析 + 列映射。"""

from io import StringIO

import pytest

from src.fetchers.acm_fetcher import fetch_acm

_CSV = StringIO(  # 源列头: RunDates, TERMYld, ACMFITYld, GSWYld
    "RunDates,TERMYld,ACMFITYld,GSWYld\n"
    "30-Jun-1961,0.16534960610894,3.86735718255489,3.87046038284282\n"
    "31-Jul-1961,0.411070179596298,4.02497578727556,4.02410640377216\n"
    "31-Jul-2026,0.837552069026338,4.82299845150996,4.83483964203813\n"
).getvalue()


def _fake_get(text: str):
    class _Resp:
        def __init__(self):
            self.text = text

        @staticmethod
        def raise_for_status():
            pass

    def _get(*a, **k):
        return _Resp()

    return _get


def test_fetch_acm_parses_and_renames(monkeypatch):
    monkeypatch.setattr("src.fetchers.acm_fetcher.requests.get", _fake_get(_CSV))

    df = fetch_acm()

    assert list(df.columns) == ["ACMTP10"]
    assert df.index.tolist() == ["1961-06-30", "1961-07-31", "2026-07-31"]
    assert df.loc["1961-06-30", "ACMTP10"] == pytest.approx(0.1653, abs=1e-4)
    assert df.loc["2026-07-31", "ACMTP10"] == pytest.approx(0.8376, abs=1e-4)


def test_fetch_acm_skips_bad_rows(monkeypatch):
    csv = StringIO(
        "RunDates,TERMYld,ACMFITYld,GSWYld\n"
        "bad-date,0.5,3.0,3.1\n"
        "31-Jan-2026,,4.0,4.1\n"
        "28-Feb-2026,0.25,4.2,4.3\n"
    ).getvalue()
    monkeypatch.setattr("src.fetchers.acm_fetcher.requests.get", _fake_get(csv))

    df = fetch_acm()
    assert df.index.tolist() == ["2026-02-28"]
    assert df.loc["2026-02-28", "ACMTP10"] == pytest.approx(0.25)


def test_fetch_acm_empty_csv(monkeypatch):
    monkeypatch.setattr("src.fetchers.acm_fetcher.requests.get", _fake_get(""))
    assert fetch_acm().empty
