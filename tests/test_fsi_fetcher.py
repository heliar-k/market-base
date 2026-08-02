"""FSI fetcher 单元测试：CSV 解析 + 列名映射。"""

from io import StringIO

from src.fetchers.fsi_fetcher import _COLUMN_RENAME, fetch_ofr_fsi

_CSV = StringIO(  # noqa: E501 源 CSV 列头很长，保持原样便于对照
    "Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility,"
    "United States,Other advanced economies,Emerging markets\n"
    "2000-01-03,2.14,0.54,-0.051,0.67,0.472,0.509,1.769,0.521,-0.15\n"
    "2000-01-04,2.421,0.604,0.079,0.627,0.55,0.561,2.084,0.474,-0.137\n"
).getvalue()


def _fake_get(text: str):
    def _get(*a, **k):
        return type("R", (), {"text": text, "raise_for_status": lambda self: None})()

    return _get


def test_fetch_parses_and_renames(monkeypatch):
    monkeypatch.setattr("src.fetchers.fsi_fetcher.requests.get", _fake_get(_CSV))

    df = fetch_ofr_fsi()

    assert list(df.columns) == list(_COLUMN_RENAME.values())[1:]
    assert df.index.tolist() == ["2000-01-03", "2000-01-04"]
    assert df.loc["2000-01-03", "OFR_FSI"] == 2.14
    assert df.loc["2000-01-04", "EMERGING_MARKETS"] == -0.137


def test_empty_csv_raises(monkeypatch):
    monkeypatch.setattr("src.fetchers.fsi_fetcher.requests.get", _fake_get(""))
    import pytest

    with pytest.raises(ValueError):
        fetch_ofr_fsi()
