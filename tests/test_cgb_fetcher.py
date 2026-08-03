"""cgb_fetcher 单元测试：RtimeYldCurv 响应解析（10Y/30Y 均值档）。"""

import pandas as pd

from src.fetchers.cgb_fetcher import fetch_cgb_curves

# 真实响应截取：8 列 [买, 卖, 均?, 待偿期, 标准期限标记, 买, 均值, 卖]
PAYLOAD = {
    "data": {
        "date": "2026-08-03",
        "data": [
            ["1.1498", "1.0999", "1.0499", "0.2", "", "", "", ""],
            ["", "", "", "0.3671", "", "1.102", "1.067", "1.0321"],
            ["", "", "", "5.0", "", "1.4266", "1.4259", "1.4252"],
            ["", "", "", "10.0", "true", "", "", ""],  # 标准期限标记行（无数据）
            ["", "", "", "10.0", "", "1.7194", "1.7145", "1.7096"],
            ["", "", "", "30.0", "", "2.1928", "2.1889", "2.185"],
        ],
    }
}


class _FakeSession:
    headers: dict = {}

    def mount(self, *args):
        pass

    def post(self, url, *a, **k):
        return type(
            "R",
            (),
            {"json": lambda _self: PAYLOAD, "raise_for_status": lambda _self: None},
        )()


def test_parses_10y_30y_mid(monkeypatch):
    """只取 10Y/30Y 的均值档（idx6），列名 cgb_10y / cgb_30y。"""
    monkeypatch.setattr(
        "src.fetchers.cgb_fetcher.requests.Session", lambda: _FakeSession()
    )
    df = fetch_cgb_curves()
    assert df.shape == (1, 2)
    assert list(df.columns) == ["cgb_10y", "cgb_30y"]
    assert df.index[0] == "2026-08-03"
    assert df.iloc[0]["cgb_10y"] == 1.7145
    assert df.iloc[0]["cgb_30y"] == 2.1889


def test_empty_when_no_standard_tenors(monkeypatch):
    """响应里没有 10/30 标准点 → 空 DataFrame（不写文件）。"""
    monkeypatch.setattr(
        "src.fetchers.cgb_fetcher.requests.Session",
        lambda: type(
            "S",
            (),
            {
                "headers": {},
                "mount": lambda *a, **k: None,
                "post": lambda *a, **k: type(
                    "R",
                    (),
                    {
                        "json": lambda _s: {"data": {"date": "2026-08-03", "data": []}},
                        "raise_for_status": lambda _s: None,
                    },
                )(),
            },
        )(),
    )
    df = fetch_cgb_curves()
    assert df.empty


def test_fallback_date_when_missing(monkeypatch):
    """API 未带 date 时回退到运行日。"""
    p = {"data": {"data": [["", "", "", "10.0", "", "1.7", "1.71", "1.72"]]}}
    monkeypatch.setattr(
        "src.fetchers.cgb_fetcher.requests.Session",
        lambda: type(
            "S",
            (),
            {
                "headers": {},
                "mount": lambda *a, **k: None,
                "post": lambda *a, **k: type(
                    "R", (), {"json": lambda _s: p, "raise_for_status": lambda _s: None}
                )(),
            },
        )(),
    )
    df = fetch_cgb_curves()
    assert df.index[0] == pd.Timestamp.now().strftime("%Y-%m-%d")
