"""ACM fetcher 单元测试：xls 解析（假 book）+ 全链路（mock 网络与 xlrd）。"""

import pytest

from src.fetchers.acm_fetcher import _parse_daily, fetch_acm


class _FakeSheet:
    """模拟 xlrd sheet：DATE + ACMY10 + ACMTP10 列。"""

    def __init__(self, rows: list[list]):
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = len(rows[0])

    def cell_value(self, r: int, c: int):
        return self._rows[r][c]


class _FakeBook:
    """模拟 xlrd book：只有 ACM Daily sheet。"""

    def __init__(self, rows: list[list]):
        self._rows = rows

    def sheet_by_name(self, name: str) -> _FakeSheet:
        assert name == "ACM Daily"
        return _FakeSheet(self._rows)


_ROWS = [
    ["DATE", "ACMY10", "ACMTP10", "ACMRNY10"],
    ["30-Jun-1961", 3.867, 0.1653, 3.7],
    ["31-Jul-1961", 4.025, 0.4111, 3.6],
    ["06-Aug-2026", 4.7533, 0.7753, 3.98],
]


def test_parse_daily_extracts_tp10_only():
    df = _parse_daily(_FakeBook(_ROWS))

    assert list(df.columns) == ["ACMTP10"]
    assert df.index.tolist() == ["1961-06-30", "1961-07-31", "2026-08-06"]
    assert df.loc["2026-08-06", "ACMTP10"] == pytest.approx(0.7753)


def test_parse_daily_skips_bad_rows():
    rows = [
        ["DATE", "ACMTP10"],
        ["bad-date", 0.5],
        ["31-Jan-2026", None],
        ["28-Feb-2026", 0.25],
    ]
    df = _parse_daily(_FakeBook(rows))
    assert df.index.tolist() == ["2026-02-28"]
    assert df.loc["2026-02-28", "ACMTP10"] == pytest.approx(0.25)


def test_parse_daily_missing_column_raises():
    with pytest.raises(ValueError):
        _parse_daily(_FakeBook([["DATE", "ACMY10"], ["30-Jun-1961", 3.9]]))


def test_fetch_acm_end_to_end(monkeypatch):
    class _Resp:
        content = b"fake-xls-bytes"

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(
        "src.fetchers.acm_fetcher.requests.get", lambda *a, **k: _Resp()
    )
    monkeypatch.setattr(
        "src.fetchers.acm_fetcher.xlrd.open_workbook",
        lambda **k: _FakeBook(_ROWS),
    )

    df = fetch_acm()
    assert df.index.tolist() == ["1961-06-30", "1961-07-31", "2026-08-06"]
    assert df.loc["1961-06-30", "ACMTP10"] == pytest.approx(0.1653)
