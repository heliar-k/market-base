from datetime import datetime
from math import isclose
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yfinance as yf

import src.compute_gex as cg


def test_fetch_options_greeks_waits_for_late_oi(monkeypatch):
    expiration = "20261120"
    monkeypatch.setattr(
        cg,
        "_load_conid_cache",
        lambda: {
            cg._conid_key("TEST", expiration, 100.0, "C"): 123,
            cg._conid_key("TEST", expiration, 100.0, "P"): 124,
        },
    )

    class Greeks:
        delta = 0.5
        gamma = 0.01
        theta = -0.02
        vega = 0.1
        impliedVol = 0.2

    class Ticker:
        def __init__(self):
            self.contract = None
            self.bid = 1.0
            self.ask = 1.1
            self.last = 1.05
            self.volume = 10
            self.modelGreeks = Greeks()
            self.callOpenInterest = float("nan")
            self.putOpenInterest = float("nan")

    class FakeIB:
        def __init__(self, oi_at=7):
            self.ticker = Ticker()
            self.sleeps = 0
            self.oi_at = oi_at

        def reqMarketDataType(self, value):
            pass

        def reqMktData(self, contract, generic, snapshot, regulatory):
            self.ticker.contract = contract
            return self.ticker

        def sleep(self, seconds):
            self.sleeps += seconds
            if self.sleeps == self.oi_at:
                self.ticker.callOpenInterest = 100.0
                self.ticker.putOpenInterest = 100.0

        def cancelMktData(self, contract):
            pass

    ib = FakeIB()
    out = cg.fetch_options_greeks(ib, "TEST", [expiration], [100.0], batch_size=2)
    assert ib.sleeps == cg.GREEKS_MIN_WAIT
    assert out.iloc[0]["openInterest"] == 100

    ib.ticker.modelGreeks.gamma = None
    out = cg.fetch_options_greeks(ib, "TEST", [expiration], [100.0], batch_size=2)
    assert out["gamma"].isna().all()

    late_ib = FakeIB(oi_at=12)
    out = cg.fetch_options_greeks(late_ib, "TEST", [expiration], [100.0], batch_size=2)
    assert late_ib.sleeps == 12
    assert out["openInterest"].tolist() == [100, 100]


def test_empty_ibkr_chain_falls_back_to_yfinance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST"])

    class FakeIB:
        disconnected = False

        def disconnect(self):
            self.disconnected = True

    ib = FakeIB()
    monkeypatch.setattr(cg, "connect_ib", lambda ports: (ib, None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [])

    class Ticker:
        options = ("20991218",)
        fast_info = type("FastInfo", (), {"last_price": 100.0})()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: Ticker())
    monkeypatch.setattr(
        "src.fetchers.barchart_options_fetcher.fetch_barchart_chain",
        lambda symbol, exps: pd.DataFrame(),
    )
    chain = pd.DataFrame(
        [
            {
                "expiration": "20991218",
                "strike": 100.0,
                "right": "C",
                "openInterest": 100,
                "volume": 10,
                "impliedVolatility": 0.2,
            }
        ]
    )
    monkeypatch.setattr(cg, "fetch_yf_chain", lambda symbol, exps: chain)

    cg.main()

    assert ib.disconnected
    assert list((tmp_path / "data" / "gex").glob("TEST_gex_*.csv"))


def test_compute_gex_preserves_ibkr_oi_and_distinguishes_zero():
    keys = {"expiration": "20261218", "strike": 100.0, "right": "C"}
    external = pd.DataFrame([{**keys, "openInterest": 0, "iv": 0.2}])

    greeks = pd.DataFrame([{**keys, "gamma": 0.01, "openInterest": 15000}])
    merged, _ = cg.compute_gex(greeks, external, 100)
    assert merged.iloc[0]["openInterest"] == 15000
    assert merged.iloc[0]["gex"] == 1500000

    greeks = pd.DataFrame([{**keys, "gamma": 0.01, "openInterest": 0}])
    merged, _ = cg.compute_gex(greeks, external.assign(openInterest=200), 100)
    assert merged.iloc[0]["openInterest"] == 0

    greeks = pd.DataFrame([{**keys, "gamma": 0.01, "openInterest": None}])
    merged, _ = cg.compute_gex(greeks, external.assign(openInterest=200), 100)
    assert merged.iloc[0]["openInterest"] == 200


def test_greeks_from_yf_uses_fractional_zero_dte_and_et(monkeypatch):
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    chain = pd.DataFrame(
        [
            {
                "expiration": "20261027",
                "strike": 100.0,
                "right": "C",
                "impliedVolatility": 0.2,
            }
        ]
    )
    out = cg.greeks_from_yf(chain, 100.0)
    from src.pricing import bs_greeks

    expected, _ = bs_greeks(100.0, 100.0, 6 / 24 / 365, 0.2)
    assert isclose(out.iloc[0]["gamma"], expected, rel_tol=1e-12)

    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 16, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert cg.greeks_from_yf(chain, 100.0).empty


def test_greeks_from_yf_empty_output_keeps_gamma_column(monkeypatch):
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    chain = pd.DataFrame(
        [
            {
                "expiration": "20261027",
                "strike": 100.0,
                "right": "C",
                "impliedVolatility": 0.001,
            }
        ]
    )
    out = cg.greeks_from_yf(chain, 100.0)
    assert out.empty
    assert "gamma" in out.columns


def test_greeks_from_yf_excludes_iv_at_or_below_one_percent(monkeypatch):
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    chain = pd.DataFrame(
        [
            {
                "expiration": "20261027",
                "strike": 100.0,
                "right": right,
                "impliedVolatility": iv,
            }
            for right, iv in (("C", 0.2), ("P", 0.01))
        ]
    )
    out = cg.greeks_from_yf(chain, 100.0)
    assert out["right"].tolist() == ["C"]


def test_filter_options_includes_same_day_expiry(monkeypatch):
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    chain = type(
        "Chain",
        (),
        {"expirations": ["20261027", "20261030", "20261106"], "strikes": [100.0]},
    )()
    expirations, _ = cg.filter_options(chain, 100.0)
    assert expirations[0] == "20261027"
    assert "20261030" in expirations


def test_expired_contracts_are_filtered_for_real_gamma_sources(monkeypatch):
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 16, 1, tzinfo=ZoneInfo("America/New_York")),
    )
    chain = type("Chain", (), {"expirations": ["20261027", "20261030"], "strikes": [100.0]})()
    expirations, _ = cg.filter_options(chain, 100.0)
    assert expirations == ["20261030"]

    greeks = pd.DataFrame(
        [
            {"expiration": exp, "strike": 100.0, "right": "C", "gamma": 0.01}
            for exp in ("20261027", "20261030")
        ]
    )
    external = greeks.assign(openInterest=100, iv=0.2)[
        ["expiration", "strike", "right", "openInterest", "iv"]
    ]
    merged, _ = cg.compute_gex(greeks, external, 100.0)
    assert merged["expiration"].tolist() == ["20261030"]


def test_print_wall_uses_cumulative_gex_flip(capsys):
    wall = pd.DataFrame(
        {
            "strike": [90.0, 100.0, 110.0],
            "call_gex": [0.0, 10.0, 0.0],
            "put_gex": [-5.0, 0.0, -1.0],
            "total_gex": [-5.0, 10.0, -1.0],
            "total_oi": [100, 100, 100],
            "avg_iv": [0.2, 0.2, 0.2],
        }
    )
    cg.print_wall(wall, 100.0, top_n=1)
    out = capsys.readouterr().out
    assert "Gamma Flip: $100" in out
    assert "Gamma Flip 区域" not in out


def test_partial_barchart_chain_falls_back_to_complete_yfinance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST", "--expirations", "2"])

    class FakeIB:
        def disconnect(self):
            pass

    monkeypatch.setattr(cg, "connect_ib", lambda ports: (FakeIB(), None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [])

    class Ticker:
        options = ("20991120", "20991127")
        fast_info = type("FastInfo", (), {"last_price": 100.0})()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: Ticker())
    partial = pd.DataFrame(
        [
            {
                "expiration": "20991120",
                "strike": 100.0,
                "right": "C",
                "gamma": 0.01,
                "iv": 0.2,
                "openInterest": 100,
                "volume": 10,
            }
        ]
    )
    monkeypatch.setattr(
        "src.fetchers.barchart_options_fetcher.fetch_barchart_chain",
        lambda symbol, exps: partial,
    )
    complete = pd.DataFrame(
        [
            {
                "expiration": exp,
                "strike": 100.0,
                "right": "C",
                "openInterest": 100,
                "volume": 10,
                "impliedVolatility": 0.2,
            }
            for exp in Ticker.options
        ]
    )
    monkeypatch.setattr(cg, "fetch_yf_chain", lambda symbol, exps: complete)

    cg.main()

    files = list((tmp_path / "data" / "gex").glob("TEST_gex_*.csv"))
    assert len(files) == 1
    out = pd.read_csv(files[0], dtype={"expiration": str})
    assert set(out["expiration"]) == set(Ticker.options)


def test_yfinance_fallback_filters_expired_before_limit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 16, 1, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST", "--expirations", "1"])

    class FakeIB:
        def disconnect(self):
            pass

    monkeypatch.setattr(cg, "connect_ib", lambda ports: (FakeIB(), None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [])

    class Ticker:
        options = ("2026-10-27", "2026-10-30", "2026-11-06")
        fast_info = type("FastInfo", (), {"last_price": 100.0})()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: Ticker())
    monkeypatch.setattr(
        "src.fetchers.barchart_options_fetcher.fetch_barchart_chain",
        lambda symbol, exps: pd.DataFrame(),
    )
    seen = []

    def _fetch(symbol, expirations):
        seen.extend(expirations)
        return pd.DataFrame(
            [
                {
                    "expiration": "20261030",
                    "strike": 100.0,
                    "right": "C",
                    "openInterest": 100,
                    "volume": 10,
                    "impliedVolatility": 0.2,
                }
            ]
        )

    monkeypatch.setattr(cg, "fetch_yf_chain", _fetch)
    cg.main()
    assert seen == ["20261030"]
    files = list((tmp_path / "data" / "gex").glob("TEST_gex_*.csv"))
    out = pd.read_csv(files[0], dtype={"expiration": str})
    assert out["expiration"].tolist() == ["20261030"]


def test_main_rejects_expiration_with_only_nan_gamma(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST"])

    class FakeIB:
        def disconnect(self):
            pass

    monkeypatch.setattr(cg, "connect_ib", lambda ports: (FakeIB(), None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    chain = type("Chain", (), {"expirations": ["20261120", "20261127"], "strikes": [100.0]})()
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [chain])
    greeks = pd.DataFrame(
        [
            {
                "expiration": exp,
                "strike": 100.0,
                "right": "C",
                "gamma": gamma,
                "iv": 0.2,
                "openInterest": 100,
            }
            for exp, gamma in (("20261120", 0.01), ("20261127", float("nan")))
        ]
    )
    monkeypatch.setattr(cg, "fetch_options_greeks", lambda *args, **kwargs: greeks)
    external = greeks.drop(columns=["gamma"]).assign(volume=10, impliedVolatility=0.2)
    monkeypatch.setattr(cg, "fetch_yf_chain", lambda symbol, exps: external)
    with pytest.raises(SystemExit):
        cg.main()


def test_unhealthy_external_chain_still_fills_missing_ibkr_oi(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cg,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST"])

    class FakeIB:
        def disconnect(self):
            pass

    monkeypatch.setattr(cg, "connect_ib", lambda ports: (FakeIB(), None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    chain = type(
        "Chain",
        (),
        {"expirations": ["20261120"], "strikes": [100.0, 101.0, 102.0, 103.0, 104.0]},
    )()
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [chain])
    greeks = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": float(strike),
                "right": "C",
                "gamma": 0.01,
                "iv": 0.2,
                "openInterest": 100 if strike == 100 else None,
            }
            for strike in range(100, 105)
        ]
    )
    monkeypatch.setattr(cg, "fetch_options_greeks", lambda *args, **kwargs: greeks)
    external = greeks.drop(columns=["gamma"]).assign(
        openInterest=[100, 200, 0, 0, 0], volume=10, impliedVolatility=0.2
    )
    monkeypatch.setattr(cg, "fetch_yf_chain", lambda symbol, exps: external)

    cg.main()

    files = list((tmp_path / "data" / "gex").glob("TEST_gex_*.csv"))
    out = pd.read_csv(files[0], dtype={"expiration": str})
    assert out["openInterest"].tolist() == [100, 200, 0, 0, 0]
