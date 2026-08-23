"""Deribit 期权墙 fetcher 测试：最近到期月度口径（timsun Strike Wall 模块对齐）。"""

import pytest

from src.fetchers import crypto_derivatives_fetcher as cdf


def _row(name: str, oi: float, price: float = 60000.0) -> dict:
    return {
        "instrument_name": name,
        "open_interest": oi,
        "volume": 10,
        "underlying_price": price,
    }


def _sample_rows() -> list[dict]:
    """最近到期 23AUG26 + 远期 25DEC26；全到期墙与最近到期墙刻意不同。"""
    return [
        # 最近到期 23AUG26（call 墙 110000/300、put 墙 90000/300）
        _row("BTC-23AUG26-100000-C", 100),
        _row("BTC-23AUG26-110000-C", 300),
        _row("BTC-23AUG26-120000-C", 200),
        _row("BTC-23AUG26-90000-P", 300),
        _row("BTC-23AUG26-80000-P", 50),
        _row("BTC-23AUG26-70000-P", 150),
        # 远期 25DEC26（全到期 call 墙 100000/600、put 墙 80000/950）
        _row("BTC-25DEC26-100000-C", 500),
        _row("BTC-25DEC26-95000-C", 0),
        _row("BTC-25DEC26-80000-P", 900),
        # instrument_name 段数不对，应被跳过（不影响 OI 统计）
        {"instrument_name": "BTC-23AUG26-100000", "open_interest": 9999},
    ]


def _patch_deribit(monkeypatch, rows):
    monkeypatch.setattr(cdf, "_deribit", lambda *a, **k: rows)


def test_deribit_called_with_currency_kind(monkeypatch):
    """fetch_options 原样透传 currency / kind（ETH 同口径）。"""
    calls = []

    def fake(method, **params):
        calls.append((method, params))
        return _sample_rows()

    monkeypatch.setattr(cdf, "_deribit", fake)
    cdf.fetch_options("ETH")
    # fetch_options 原样透传 currency / kind（ETH 同口径）。
    assert calls == [
        ("get_book_summary_by_currency", {"currency": "ETH", "kind": "option"})
    ]


def test_nearest_exp_walls_and_tops(monkeypatch):
    """最近到期取最早、near 墙与全到期墙不同时两边都对、top5 降序、OI 统计。"""
    _patch_deribit(monkeypatch, _sample_rows())
    out = cdf.fetch_options("BTC")

    # 最近到期取最早（23AUG26 < 25DEC26）
    assert out["nearest_exp"] == "2026-08-23"
    assert out["d_exp"] == 2

    # 全到期口径（原有字段，值不变）
    assert out["call_wall"] == 100000.0  # 100+500
    assert out["put_wall"] == 80000.0  # 50+900
    assert out["max_pain"] is not None
    assert out["total_oi"] == 2500.0  # call 1100 + put 1400
    assert out["pcr"] == round(1400 / 1100, 2)

    # 最近到期口径：call 墙 110000/300、put 墙 90000/300（与全到期不同）
    assert out["near_call_wall"] == 110000.0
    assert out["near_call_wall_oi"] == 300.0
    assert out["near_put_wall"] == 90000.0
    assert out["near_put_wall_oi"] == 300.0

    # top5 按 OI 降序
    assert out["top_calls"] == [
        {"strike": 110000.0, "oi": 300.0},
        {"strike": 120000.0, "oi": 200.0},
        {"strike": 100000.0, "oi": 100.0},
    ]
    assert out["top_puts"] == [
        {"strike": 90000.0, "oi": 300.0},
        {"strike": 70000.0, "oi": 150.0},
        {"strike": 80000.0, "oi": 50.0},
    ]

    # near max pain：K=90000 时 call 全 OTM、put 买方价外，支付 0 为全局最小
    assert out["near_max_pain"] == 90000.0


def test_legacy_fields_present_for_backward_compat(monkeypatch):
    """原有字段全部保留：spot_anchor / call_wall / put_wall / pcr / max_pain /
    total_oi / d_exp。
    """
    _patch_deribit(monkeypatch, _sample_rows())
    out = cdf.fetch_options("BTC")
    for key in (
        "spot_anchor",
        "call_wall",
        "put_wall",
        "pcr",
        "max_pain",
        "total_oi",
        "d_exp",
    ):
        assert key in out
    assert out["spot_anchor"] == 60000.0
    assert out["max_pain"] is not None


def test_empty_response_returns_empty_dict(monkeypatch):
    """非列表响应（如 Deribit 错误返回 {}）保持原行为返回 {}。"""
    _patch_deribit(monkeypatch, "error")
    assert cdf.fetch_options("BTC") == {}


@pytest.mark.parametrize("currency", ["BTC", "ETH"])
def test_eth_same_walls_shape(monkeypatch, currency):
    """ETH 同口径：字段结构一致。"""
    _patch_deribit(monkeypatch, _sample_rows())
    out = cdf.fetch_options(currency)
    assert out["nearest_exp"] == "2026-08-23"
    assert out["near_call_wall_oi"] == 300.0
    assert len(out["top_puts"]) == 3


def test_yahoo_quote_oi_parses(monkeypatch):
    """_yahoo_quote_oi：crumb 两步流程，解析 BTC=F / MBT=F 的 OI。"""
    import requests

    calls = []

    class _FakeResp:
        def __init__(self, text, payload=None):
            self.text = text
            self._payload = payload or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            calls.append(url)
            if "test/getcrumb" in url:
                return _FakeResp("crumb123")
            return _FakeResp(
                "",
                {
                    "quoteResponse": {
                        "result": [
                            {
                                "symbol": "BTC=F",
                                "openInterest": 12573,
                                "shortName": "Bitcoin Futures,Aug-2026",
                            },
                            {
                                "symbol": "MBT=F",
                                "openInterest": 31283,
                                "shortName": "Micro Bitcoin Futures,Aug-2026",
                            },
                        ]
                    }
                },
            )

    monkeypatch.setattr(requests, "Session", _FakeSession)
    out = cdf._yahoo_quote_oi()
    assert out == {
        "fut_oi": 12573,
        "fut_contract": "Bitcoin Futures,Aug-2026",
        "micro_oi": 31283,
        "micro_contract": "Micro Bitcoin Futures,Aug-2026",
    }
    assert any("test/getcrumb" in u for u in calls)


def test_yahoo_quote_oi_missing_fields(monkeypatch):
    """OI 缺失（None）的合约跳过，不报错。"""
    import requests

    class _FakeResp:
        def __init__(self, text, payload=None):
            self.text = text
            self._payload = payload or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            if "test/getcrumb" in url:
                return _FakeResp("crumb2")
            return _FakeResp(
                "",
                {
                    "quoteResponse": {
                        "result": [{"symbol": "BTC=F", "openInterest": None}]
                    }
                },
            )

    monkeypatch.setattr(requests, "Session", _FakeSession)
    assert cdf._yahoo_quote_oi() == {}
