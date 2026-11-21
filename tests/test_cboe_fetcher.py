"""CBOE fetcher 测试：CLOSE 格式 / 官方代码值列格式 / 403 跳过 / 缺列检测。"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.fetchers import cboe_fetcher
from src.fetchers.cboe_fetcher import check_latest_complete


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


# ── check_latest_complete：daily-full 判断是否派发 cboe-retry 的口径 ──


def _write_volatility(tmp_path, data: dict[str, list[float | None]], dates: list[str]):
    path = tmp_path / "volatility.csv"
    pd.DataFrame(data, index=dates).rename_axis("date").to_csv(path)
    return path


def test_latest_row_complete(tmp_path):
    path = _write_volatility(
        tmp_path,
        {"VIX": [17.2, 28.18], "SKEW": [151.03, 149.37], "VIX_TERM_SLOPE": [1.33, -4.48]},
        ["2026-10-29", "2026-11-05"],
    )
    assert check_latest_complete(path) == []


def test_latest_row_missing_columns(tmp_path):
    path = _write_volatility(
        tmp_path,
        {"VIX": [17.2, None], "SKEW": [151.03, None], "VIX_TERM_SLOPE": [1.33, None]},
        ["2026-10-29", "2026-11-05"],
    )
    assert check_latest_complete(path) == ["VIX", "SKEW", "VIX_TERM_SLOPE"]


def test_cboe_urls_include_dispersion_and_correlation():
    assert {"DSPX", "VIXEQ", "COR1M", "COR3M"}.issubset(cboe_fetcher.CBOE_URLS.keys())


def test_parse_put_call_payload_supports_legacy_spx_alias():
    payload_new = {
        "ratios": [
            {"name": "TOTAL PUT/CALL RATIO", "value": "1.01"},
            {"name": "EQUITY PUT/CALL RATIO", "value": "0.62"},
            {"name": "SPX + SPXW + SPXO PUT/CALL RATIO", "value": "1.21"},
        ]
    }
    payload_old = {
        "ratios": [
            {"name": "TOTAL PUT/CALL RATIO", "value": "0.93"},
            {"name": "SPX + SPXW PUT/CALL RATIO", "value": "1.18"},
        ]
    }
    assert cboe_fetcher._parse_put_call_payload(payload_new) == {
        "TOTAL_PC": 1.01,
        "EQUITY_PC": 0.62,
        "SPX_PC": 1.21,
    }
    assert cboe_fetcher._parse_put_call_payload(payload_old) == {
        "TOTAL_PC": 0.93,
        "SPX_PC": 1.18,
    }


def test_fetch_cboe_put_call_skips_403_and_existing_complete(monkeypatch, tmp_path):
    from datetime import date

    existing = tmp_path / "put_call.csv"
    pd.DataFrame(
        {
            "TOTAL_PC": [0.91],
            "INDEX_PC": [1.05],
            "ETP_PC": [1.10],
            "EQUITY_PC": [0.58],
            "VIX_PC": [0.80],
            "SPX_PC": [1.15],
        },
        index=["2026-11-10"],
    ).rename_axis("date").to_csv(existing)

    called: list[str] = []

    class _JsonResp:
        def __init__(self, status: int, data: dict | None = None):
            self.status_code = status
            self._data = data or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(str(self.status_code))

        def json(self):
            return self._data

    def fake_get(url: str, timeout: int = 15):
        day = url.rsplit("/", 1)[-1].split("_")[0]
        called.append(day)
        if day == "2026-11-19":
            return _JsonResp(
                200,
                {
                    "ratios": [
                        {"name": "TOTAL PUT/CALL RATIO", "value": "1.01"},
                        {"name": "INDEX PUT/CALL RATIO", "value": "1.20"},
                        {"name": "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO", "value": "1.09"},
                        {"name": "EQUITY PUT/CALL RATIO", "value": "0.62"},
                        {"name": "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO", "value": "1.10"},
                        {"name": "SPX + SPXW + SPXO PUT/CALL RATIO", "value": "1.21"},
                    ]
                },
            )
        return _JsonResp(403)

    fake_sess = MagicMock()
    fake_sess.__enter__.return_value = fake_sess
    fake_sess.__exit__.return_value = False
    fake_sess.get.side_effect = fake_get
    monkeypatch.setattr(cboe_fetcher.requests, "Session", lambda: fake_sess)

    df = cboe_fetcher.fetch_cboe_put_call(
        existing_path=existing,
        lookback_days=10,
        end_date=date(2026, 11, 20),
    )
    # 2026-11-10 已完整且早于刷新窗口，不重复请求
    assert "2026-11-10" not in called
    assert "2026-11-19" in called
    assert list(df.index) == ["2026-11-19"]
    assert df.loc["2026-11-19", "EQUITY_PC"] == 0.62
    assert df.loc["2026-11-19", "SPX_PC"] == 1.21


def test_fetch_cboe_put_call_raises_when_all_targets_fail_with_errors(monkeypatch, tmp_path):
    from datetime import date

    fake_sess = MagicMock()
    fake_sess.__enter__.return_value = fake_sess
    fake_sess.__exit__.return_value = False
    fake_sess.get.side_effect = RuntimeError("503 upstream down")
    monkeypatch.setattr(cboe_fetcher.requests, "Session", lambda: fake_sess)

    with pytest.raises(RuntimeError, match="拉取异常"):
        cboe_fetcher.fetch_cboe_put_call(
            existing_path=tmp_path / "put_call.csv",
            lookback_days=5,
            end_date=date(2026, 11, 20),
        )


def test_only_latest_row_counts(tmp_path):
    """历史日期的空洞不触发补抓：只看最新观测日。"""
    path = _write_volatility(
        tmp_path,
        {"VIX": [None, 28.18], "SKEW": [None, 149.37]},
        ["2026-10-29", "2026-11-05"],
    )
    assert check_latest_complete(path) == []


def test_missing_file(tmp_path):
    assert check_latest_complete(tmp_path / "absent.csv") == ["文件不存在"]
