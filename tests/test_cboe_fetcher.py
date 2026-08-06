"""CBOE fetcher 测试：CLOSE 格式 / 官方代码值列格式 / 403 跳过。"""

from unittest.mock import MagicMock

import pytest

from src.fetchers import cboe_fetcher


class _FakeResp:
    def __init__(self, text: str, ok: bool = True):
        self.text = text
        self.ok = ok
        self.status_code = 200 if ok else 403

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("403")


@pytest.fixture
def mock_session(monkeypatch):
    """按 URL 返回对应 CSV；未知 URL 模拟 403。"""
    csvs = {
        # CLOSE 列格式（VIX 家族）
        "GVZ_History.csv": (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "08/01/2026,25.0,25.5,24.8,25.2\n"
            "08/04/2026,25.1,25.6,24.9,25.3\n"
        ),
        # 值列=官方代码格式（VXTLT 数据），列名与配置的显示名（VTLT）不同
        "VXTLT_History.csv": "DATE,VXTLT\n08/01/2026,12.0\n08/04/2026,12.1\n",
        # 单个字符串值列
        "VXSLV_History.csv": "DATE,VXSLV\n08/01/2026,45.3\n08/04/2026,46.0\n",
    }

    def fake_get(url, timeout=30):
        for key, text in csvs.items():
            if url.endswith(key):
                return _FakeResp(text)
        return _FakeResp("", ok=False)

    session = MagicMock()
    session.get.side_effect = fake_get
    monkeypatch.setattr(cboe_fetcher.requests, "Session", lambda: session)
    # 让 fetch_cboe_volatility 只跑测试子集，避免对真实 URL 逐个打网络
    monkeypatch.setattr(
        cboe_fetcher,
        "CBOE_URLS",
        {
            "GVZ": "https://cdn.cboe.com/api/global/us_indices/daily_prices/GVZ_History.csv",
            "VTLT": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXTLT_History.csv",
            "VXSL": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXSLV_History.csv",
        },
    )
    return session


def test_closed_format_and_missing_skip(mock_session):
    df = cboe_fetcher.fetch_cboe_volatility()
    # GVZ（CLOSE 格式）+ VTLT（官方代码值列）+ VXSL（单值列）都解析到
    assert set(df.columns) >= {"GVZ", "VTLT", "VXSL"}
    # 日期归一化为 ISO
    assert df.index[0] == "2026-08-01"
    assert df["VTLT"].iloc[-1] == 12.1
    assert df["GVZ"].iloc[-1] == 25.3
    assert df["VXSL"].iloc[-1] == 46.0


def test_missing_code_skipped_without_crash(mock_session):
    # 403 的 URL（CDN 上不存在，如 VXMT）不产生列、不影响其他指数；
    # 复用 mock_session 不打网络
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        cboe_fetcher,
        "CBOE_URLS",
        {
            "GVZ": "https://cdn.cboe.com/api/global/us_indices/daily_prices/GVZ_History.csv",
            # 此 URL 在 mock 中模拟 403
            "VXMT": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXMT_History.csv",
        },
    )
    try:
        df = cboe_fetcher.fetch_cboe_volatility()
        assert "VXMT" not in df.columns
        assert "GVZ" in df.columns
    finally:
        monkeypatch.undo()
