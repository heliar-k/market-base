"""Tests for src.fetchers.cboe_options_fetcher（CBOE 延迟快照保底源）。"""

from __future__ import annotations

import pytest

import src.fetchers.cboe_options_fetcher as cf


class _Resp:
    def __init__(self, options, last_trade_time="2026-10-29T15:59:59", status_code=200) -> None:
        self.status_code = status_code
        self._payload = {
            "data": {
                "close": 213.66,
                "last_trade_time": last_trade_time,
                "options": options,
            }
        }

    def json(self) -> dict:
        return self._payload


def _opt(osi: str, oi: float = 100, iv: float = 0.4) -> dict:
    return {
        "option": osi,
        "open_interest": oi,
        "iv": iv,
        "bid": 4.0,
        "ask": 4.2,
        "volume": 10,
        "last_trade_price": 4.1,
    }


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """节流与退避的 sleep 一律跳过，测试不真等。"""
    monkeypatch.setattr(cf.time, "sleep", lambda s: None)
    monkeypatch.setattr(cf, "_last_request_at", 0.0)


def test_osi_to_parts() -> None:
    assert cf._osi_to_parts("NVDA261120P00195000") == ("20261120", 195.0, "P")
    assert cf._osi_to_parts("NVDA261120C00200000") == ("20261120", 200.0, "C")
    assert cf._osi_to_parts("BAD") is None
    assert cf._osi_to_parts("") is None


def test_quote_stamp_converts_et_to_utc() -> None:
    """last_trade_time 是美东本地时间，写进链时统一成 UTC ISO（夏/冬令时各自正确）。"""
    assert cf._quote_stamp("2026-10-29T15:59:59") == ("2026-10-29", "2026-10-29T19:59:59Z")
    assert cf._quote_stamp("2026-12-15T15:59:59") == ("2026-12-15", "2026-12-15T20:59:59Z")
    assert cf._quote_stamp(None) == (None, None)
    assert cf._quote_stamp("not-a-date") == (None, None)


def test_throttle_waits_when_interval_not_reached(monkeypatch) -> None:
    slept = []
    monkeypatch.setattr(cf, "_last_request_at", 100.0)
    monkeypatch.setattr(cf.time, "monotonic", lambda: 100.2)
    monkeypatch.setattr(cf.time, "sleep", lambda s: slept.append(s))

    cf._throttle()
    assert len(slept) == 1
    assert slept[0] == pytest.approx(0.8)
    assert cf._last_request_at == pytest.approx(100.2)


def test_throttle_skips_wait_when_interval_passed(monkeypatch) -> None:
    slept = []
    monkeypatch.setattr(cf, "_last_request_at", 100.0)
    monkeypatch.setattr(cf.time, "monotonic", lambda: 102.0)
    monkeypatch.setattr(cf.time, "sleep", lambda s: slept.append(s))

    cf._throttle()
    assert slept == []


def test_retries_connection_error_then_succeeds(monkeypatch) -> None:
    calls = []

    def _get(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise cf.requests.RequestException("boom")
        return _Resp([_opt("NVDA261120P00195000")])

    monkeypatch.setattr(cf.requests, "get", _get)
    df, _ = cf.fetch_cboe_chain("NVDA")
    assert len(calls) == 2
    assert len(df) == 1


def test_retries_5xx_then_succeeds(monkeypatch) -> None:
    calls = []

    def _get(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return _Resp([], status_code=503)
        return _Resp([_opt("NVDA261120P00195000")])

    monkeypatch.setattr(cf.requests, "get", _get)
    df, _ = cf.fetch_cboe_chain("NVDA")
    assert len(calls) == 2
    assert len(df) == 1


def test_4xx_not_retried(monkeypatch) -> None:
    """403 是路径符号写错或源上已下架，重试只会拖慢。"""
    calls = []

    def _get(*_a, **_k):
        calls.append(1)
        return _Resp([], status_code=403)

    monkeypatch.setattr(cf.requests, "get", _get)
    assert cf.fetch_cboe_chain("NVDA") is None
    assert len(calls) == 1


def test_retry_exhausted_returns_none(monkeypatch) -> None:
    calls = []

    def _get(*_a, **_k):
        calls.append(1)
        raise cf.requests.RequestException("boom")

    monkeypatch.setattr(cf.requests, "get", _get)
    assert cf.fetch_cboe_chain("NVDA") is None
    assert len(calls) == len(cf._RETRY_BACKOFF) + 1


def test_fetch_cboe_chain_maps_columns_and_filters_expirations(monkeypatch) -> None:
    opts = [
        _opt("NVDA261120P00195000", oi=15000, iv=0.42),
        _opt("NVDA261120C00200000", oi=12000, iv=0.40),
        _opt("NVDA270115C00250000", oi=5, iv=0.5),
        {"option": "BAD-ROW", "open_interest": 1, "iv": 0.3},
    ]
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp(opts))

    df, chain_date = cf.fetch_cboe_chain("NVDA", ["20261120"])
    assert chain_date == "2026-10-29"
    assert df["expiration"].tolist() == ["20261120", "20261120"]
    assert df["strike"].tolist() == [195.0, 200.0]
    assert df["right"].tolist() == ["P", "C"]
    assert df["openInterest"].tolist() == [15000, 12000]
    assert df["impliedVolatility"].tolist() == [0.42, 0.40]
    assert df["bid"].tolist() == [4.0, 4.0]
    assert set(df["quote_ts"]) == {"2026-10-29T19:59:59Z"}

    full, _ = cf.fetch_cboe_chain("NVDA")
    assert len(full) == 3


def test_fetch_cboe_chain_uses_dotted_path_for_dashed_symbol(monkeypatch) -> None:
    """BRK-B 要请求 BRK.B（带横线的路径返回 403）。"""
    seen = {}

    def _get(url, **_kw):
        seen["url"] = url
        return _Resp([_opt("BRK.B261120P00500000")])

    monkeypatch.setattr(cf.requests, "get", _get)
    df, _ = cf.fetch_cboe_chain("BRK-B")
    assert seen["url"].endswith("/BRK.B.json")
    assert len(df) == 1
    assert df["strike"].tolist() == [500.0]


def test_missing_oi_becomes_zero(monkeypatch) -> None:
    """OI 缺失归一为 0：下游 compute_structure 用 int(oi) 取值，NaN 会抛 ValueError。"""
    opt = _opt("NVDA261120P00195000")
    opt["open_interest"] = None
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp([opt]))
    df, _ = cf.fetch_cboe_chain("NVDA")
    assert df["openInterest"].tolist() == [0]


def test_empty_expirations_means_no_filter(monkeypatch) -> None:
    opts = [_opt("NVDA261120P00195000"), _opt("NVDA270115C00250000")]
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp(opts))
    df, _ = cf.fetch_cboe_chain("NVDA", [])
    assert len(df) == 2


def test_missing_quote_stamp_returns_none(monkeypatch) -> None:
    """没有 last_trade_time 就无法标注快照属于哪个交易日，按不可用处理。"""
    monkeypatch.setattr(
        cf.requests, "get", lambda *a, **k: _Resp([_opt("NVDA261120P00195000")], None)
    )
    assert cf.fetch_cboe_chain("NVDA") is None
