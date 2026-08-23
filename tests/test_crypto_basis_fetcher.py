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


def test_month_end_rolls_to_next_contract(monkeypatch):
    """月末最后周五之后（2026-06-26 后）→ 滚到次月合约，dte 为正，不再被丢弃。

    2026-06-29/30：近月应取 2026-07-31（周五），dte = 32/31；
    修复前 _expiry 返回 06-26 → dte 为负被规则 1 静默滤掉（每月丢 1-3 交易日）。
    """
    dates = ["2026-06-29", "2026-06-30", "2026-12-31"]
    fut = [100.5, 100.6, 100.5]
    _mock_yahoo(monkeypatch, dates, fut, dates, [100.0] * 3)
    df = cbf.fetch_basis_series()
    # 末根（12-31）跳过；06-29/30 保留，dte 分别 32/31
    assert list(df.index) == [
        pd.Timestamp("2026-06-29"),
        pd.Timestamp("2026-06-30"),
    ]
    assert df.iloc[0]["basis_pct"] == pytest.approx(5.70, abs=0.01)
    assert df.iloc[1]["basis_pct"] == pytest.approx(7.06, abs=0.01)


def test_single_bar_degrades_to_empty(monkeypatch):
    """只剩 1 行 → 规则 3 跳过末根后为空，返回空 df（不崩）。"""
    _mock_yahoo(monkeypatch, ["2026-01-05"], [100.5], ["2026-01-05"], [100.0])
    df = cbf.fetch_basis_series()
    assert df.empty
    assert list(df.columns) == ["basis_pct", "fut_close", "spot_close"]


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        ("2026-01-05", "2026-01-30"),  # 正常：当月最后周五
        ("2026-06-29", "2026-07-31"),  # 月末段（最后周五之后）→ 次月
        ("2026-06-30", "2026-07-31"),
        ("2026-12-31", "2027-01-29"),  # 跨年 roll
        ("2026-07-31", "2026-07-31"),  # 月末本身是周五 → dte=0（规则 1 滤）
        ("2026-01-30", "2026-01-30"),  # 周五非月末日 → dte=0（规则 1 滤）
    ],
)
def test_expiry_edges(d: str, expected: str):
    """_expiry 边界：正常 / 月末后段 roll / 跨年 / 到期日当天（dte=0 交规则 1）。"""
    assert cbf._expiry(pd.Timestamp(d)) == pd.Timestamp(expected)
