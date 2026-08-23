"""crypto_basis_fetcher 单元测试：Yahoo chart 伪造响应 + timsun V1 治理规则。"""

import pandas as pd
import pytest

from src.fetchers import crypto_basis_fetcher as cbf


def _ts(d: str) -> int:
    """chart API 时间戳（NY 当日零点，normalize 后等于该日期）。"""
    return int(pd.Timestamp(d, tz="America/New_York").timestamp())


def _payload(dates: list[str], closes: list[float | None]) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [_ts(d) for d in dates],
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def _mock_yahoo(
    monkeypatch,
    fut_dates: list[str],
    fut_closes: list[float | None],
    spot_dates: list[str],
    spot_closes: list[float | None],
) -> None:
    monkeypatch.setattr(cbf, "ensure_yf_proxy", lambda *a, **kw: None)

    def fake_get(url, *a, **kw):
        payload = (
            _payload(fut_dates, fut_closes)
            if "BTC=F" in url
            else _payload(spot_dates, spot_closes)
        )
        return _FakeResp(payload)

    monkeypatch.setattr(cbf.requests, "get", fake_get)


def test_formula_and_latest_bar_skipped(monkeypatch):
    """公式正确性 + 最后一根未成熟日线跳过。

    2026-01-05：dte = 25（2026-01-30 为当月最后一个周五），
    basis = (100.5/100 − 1) × 365/25 × 100 = 7.3；2026-01-06 是最后一行 → 跳过。
    """
    _mock_yahoo(
        monkeypatch,
        ["2026-01-05", "2026-01-06"],
        [100.5, 100.6],
        ["2026-01-05", "2026-01-06"],
        [100.0, 100.0],
    )
    df = cbf.fetch_basis_series()
    assert list(df.columns) == ["basis_pct", "fut_close", "spot_close"]
    assert list(df.index) == [pd.Timestamp("2026-01-05")]
    row = df.iloc[0]
    assert row["fut_close"] == 100.5
    assert row["spot_close"] == 100.0
    assert row["basis_pct"] == pytest.approx(7.3)


def test_roll_period_and_outlier_filtered(monkeypatch):
    """dte<10（roll 过渡期）与 |basis_pct|>18%（失真）行被过滤。

    2026-01-08：(118/100−1)×365/22×100 ≈ 298% > 18% → 规则 2；
    2026-01-30：dte = 0 < 10 → 规则 1；2026-02-02：最后一行 → 规则 3。
    仅 2026-01-05、2026-01-06 保留（正常窗口内）。
    """
    dates = ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-30", "2026-02-02"]
    fut = [100.5, 100.6, 118.0, 107.0, 100.8]
    _mock_yahoo(monkeypatch, dates, fut, dates, [100.0] * 5)
    df = cbf.fetch_basis_series()
    assert list(df.index) == [
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-01-06"),
    ]
    assert df.iloc[0]["basis_pct"] == pytest.approx(7.3)
    assert df.iloc[1]["basis_pct"] == pytest.approx(9.12, abs=0.02)
